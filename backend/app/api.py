"""백오피스 API. 집계는 app/reports.py, 로그인·권한은 app/auth.py에 있다.

로컬 실행 (backend 폴더): uvicorn app.api:app --reload
문서: http://127.0.0.1:8000/docs

/api/health와 /api/auth/login을 빼면 모두 로그인이 필요하고, 조회 API는 사장님 계정만 쓸 수 있다.
데모 서버(DEMO_MODE=1)에서만 /api/auth/demo로 비밀번호 없이 데모 계정에 들어갈 수 있다.
"""

import logging
import os
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from threading import Lock
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select, text

from app import models as m
from app import reports
from app.demo import DEMO_EMAIL, DEMO_STAFF_EMAIL, demo_mode
from app.auth import COOKIE, Owner, User, authenticate, cookie_options, make_token
from app.deps import DB, get_session  # noqa: F401  테스트가 app.api.get_session을 바꿔 끼운다

log = logging.getLogger("songhak.api")

app = FastAPI(title="송학구이 백오피스 API", version="0.2.0")

# 입력 검증. 앞뒤 공백을 먼저 지운 뒤 길이를 잰다 (공백만 있는 질문이 AI 호출까지 가지 않게)
QUESTION_MAX = 300
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=QUESTION_MAX)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254)]
# 비밀번호 해시(scrypt)는 입력이 길수록 오래 걸리므로 상한을 둔다. 계정 만들 때도 같은 상한(app/users.py)
PASSWORD_MAX = 128
Password = Annotated[str, StringConstraints(min_length=1, max_length=PASSWORD_MAX)]

# 검증 실패(422)를 화면에 그대로 보여 줄 수 있는 한 문장으로. 필드 이름 → 안내 문구
# 지출 입력 상한. 금액 상한은 자릿수를 잘못 친 입력(0을 하나 더)을 막는 용도
VENDOR_MAX, MEMO_MAX, AMOUNT_MAX = 60, 200, 100_000_000
FIELD_MESSAGES = {
    "question": f"질문은 2~{QUESTION_MAX}자로 입력해 주세요.",
    "email": "이메일을 확인해 주세요.",
    "password": f"비밀번호를 입력해 주세요 ({PASSWORD_MAX}자 이하).",
    "month": "월은 2026-09처럼 YYYY-MM 형식이어야 합니다.",
    "spent_on": "날짜를 확인해 주세요.",
    "vendor": f"산 곳을 1~{VENDOR_MAX}자로 입력해 주세요.",
    "category": "분류를 골라 주세요.",
    "amount": f"금액은 1원 이상 {AMOUNT_MAX // 10_000:,}만 원 이하의 숫자로 입력해 주세요.",
    "memo": f"메모는 {MEMO_MAX}자 이하로 입력해 주세요.",
}

Month = Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM", examples=["2026-09"])]


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """detail은 사람이 읽는 한 문장(화면이 그대로 보여 준다), errors는 원래의 상세 목록."""
    errors = exc.errors()
    first = errors[0] if errors else {}
    field = str(first.get("loc", ["", ""])[-1])
    if field in FIELD_MESSAGES:
        message = FIELD_MESSAGES[field]
    elif first.get("type") == "json_invalid" or not field or field.isdigit():  # JSON이 깨졌거나 본문 자체가 이상함
        message = "요청 형식이 올바르지 않습니다."
    else:
        message = f"입력값을 확인해 주세요: {field}"
    return JSONResponse(status_code=422, content={"detail": message, "errors": jsonable_encoder(errors)})


@app.get("/api/health")
def health(s: DB) -> dict:
    s.execute(text("select 1"))
    return {"status": "ok"}


# ── 로그인 ──


class LoginBody(BaseModel):
    email: Email
    password: Password


def user_out(u) -> dict:
    return {"id": u.id, "email": u.email, "role": u.role}


@app.post("/api/auth/login", summary="로그인. 성공하면 HttpOnly 쿠키를 준다")
def login(body: LoginBody, s: DB, response: Response) -> dict:
    user = authenticate(s, body.email, body.password)
    response.set_cookie(COOKIE, make_token(user), **cookie_options())
    return user_out(user)


@app.post("/api/auth/demo", summary="데모 서버에서만: 비밀번호 없이 데모 계정(사장님 또는 직원)으로 로그인")
def demo_login(s: DB, response: Response, role: Literal["owner", "staff"] = "owner") -> dict:
    if not demo_mode():
        raise HTTPException(404, "Not Found")
    email = DEMO_STAFF_EMAIL if role == "staff" else DEMO_EMAIL
    user = s.scalar(select(m.AppUser).where(m.AppUser.email == email, m.AppUser.is_active))
    if user is None:
        raise HTTPException(503, "데모 데이터가 아직 준비되지 않았습니다")
    response.set_cookie(COOKIE, make_token(user), **cookie_options())
    return user_out(user)


@app.post("/api/auth/logout", summary="로그아웃")
def logout(response: Response) -> dict:
    opts = cookie_options()
    response.delete_cookie(COOKIE, path=opts["path"], secure=opts["secure"], httponly=True, samesite="lax")
    return {"ok": True}


@app.get("/api/auth/me", summary="지금 로그인한 계정")
def me(user: User) -> dict:
    return user_out(user)


# ── 직원 본인 ──


@app.get("/api/me/pay", summary="직원 본인: 급여 기간의 근무 기록과 (예상) 급여")
def my_pay(s: DB, user: User, month: Month | None = None) -> dict:
    # 누구의 기록인지는 로그인 토큰으로만 정한다. 직원 ID를 받는 칸이 없어서 다른 사람 것을 물을 방법이 없다
    if user.employee_id is None:
        raise HTTPException(404, "직원과 연결된 계정이 아닙니다")
    pay = reports.my_pay(s, user.employee_id, month or reports.current_pay_month(reports.today_kst()))
    if pay is None:
        raise HTTPException(404, "직원 정보를 찾을 수 없습니다")
    return pay


# ── 조회 (사장님) ──


@app.get("/api/home", summary="홈: 이번 급여 기간 인건비, 이번 달 정산, 최근 발주, 마지막 동기화")
def home(s: DB, _: Owner, day: date | None = None) -> dict:
    return reports.home(s, day)


@app.get("/api/settlements", summary="정산처별 월 정산 합계")
def settlements(s: DB, _: Owner, month: Month) -> dict:
    return reports.settlements(s, month)


@app.get("/api/settlements/{place_id}", summary="정산서: 한 정산처의 월 주문·항목 내역")
def settlement_detail(s: DB, _: Owner, place_id: int, month: Month) -> dict:
    detail = reports.settlement_detail(s, place_id, month)
    if detail is None:
        raise HTTPException(404, "정산처를 찾을 수 없습니다")
    return detail


@app.get("/api/settlements/{place_id}/pdf", summary="정산서 PDF 내려받기", response_class=Response,
         responses={200: {"content": {"application/pdf": {}}}})
def settlement_pdf(s: DB, _: Owner, place_id: int, month: Month) -> Response:
    from urllib.parse import quote

    from app.invoices import file_name, render

    detail = reports.settlement_detail(s, place_id, month)
    if detail is None:
        raise HTTPException(404, "정산처를 찾을 수 없습니다")
    # 한글 파일 이름은 filename*(UTF-8)로, 옛 브라우저용 filename은 영문으로 준다
    disposition = f"attachment; filename=\"invoice-{month}-{place_id}.pdf\"; filename*=UTF-8''{quote(file_name(detail))}"
    return Response(render(detail), media_type="application/pdf", headers={"Content-Disposition": disposition})


@app.get("/api/payroll", summary="급여 기간(전월 17일 ~ 당월 16일)의 직원별 인건비")
def payroll(s: DB, _: Owner, month: Month) -> dict:
    return reports.payroll(s, month)


@app.get("/api/supply-orders", summary="발주 목록과 비용 (결제 금액 우선, 없으면 예상 금액)")
def supply_orders(
    s: DB,
    _: Owner,
    start: date | None = None,
    end: date | None = None,
    status: Literal["pending", "ordered", "done"] | None = None,
    include_deleted: bool = True,
) -> dict:
    if start and end and start > end:
        raise HTTPException(422, "start가 end보다 늦습니다")
    return reports.supply_orders(s, start, end, status, include_deleted)


# ── 지출 · 손익 (사장님) ──

Vendor = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=VENDOR_MAX)]
Memo = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MEMO_MAX)]


class ExpenseBody(BaseModel):
    spent_on: date
    vendor: Vendor
    category: Literal[m.EXPENSE_CATEGORIES]  # type: ignore[valid-type]
    amount: Annotated[int, Field(strict=True, gt=0, le=AMOUNT_MAX)]
    memo: Memo | None = None


@app.get("/api/expenses", summary="한 달(달력 기준)의 지출 목록과 분류별 합계")
def expenses(s: DB, _: Owner, month: Month) -> dict:
    return reports.expenses(s, month)


@app.post("/api/expenses", status_code=201, summary="지출 한 건 입력")
def add_expense(body: ExpenseBody, s: DB, user: Owner) -> dict:
    if body.spent_on > reports.today_kst():
        raise HTTPException(422, "앞으로의 날짜는 넣을 수 없습니다")
    x = m.Expense(spent_on=body.spent_on, vendor=body.vendor, category=body.category, amount=body.amount,
                  memo=body.memo or None, source="manual", created_by=user.id)
    s.add(x)
    s.commit()
    return {"id": x.id, "spent_on": x.spent_on, "vendor": x.vendor, "category": x.category, "amount": x.amount,
            "memo": x.memo, "source": x.source}


@app.delete("/api/expenses/{expense_id}", summary="지출 지우기 (기록은 남기고 목록·합계에서 뺀다)")
def delete_expense(expense_id: int, s: DB, _: Owner) -> dict:
    x = s.get(m.Expense, expense_id)
    if x is None or x.deleted_at is not None:
        raise HTTPException(404, "지출을 찾을 수 없습니다")
    x.deleted_at = datetime.now(timezone.utc)
    s.commit()
    return {"id": expense_id, "deleted": True}


@app.get("/api/profit", summary="월 손익 요약: 매출(홀 매출 + 외상 정산) − 비용(지출 + 발주 + 인건비)")
def profit(s: DB, _: Owner, month: Month) -> dict:
    return reports.profit(s, month)


# ── 홀 매출: OK POS 엑셀 (사장님) ──
# 파일은 multipart가 아니라 본문 그대로 받는다 (새 의존성 없이). Content-Type을 엑셀로 정해 둬서
# 다른 사이트의 폼으로는 보낼 수 없다 (폼이 쓸 수 있는 형식이 아니다). 쿠키도 SameSite=lax다.
XLSX_TYPES = {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"}


async def xlsx_body(request: Request) -> bytes:
    from app.pos_import import MAX_UPLOAD

    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in XLSX_TYPES:
        raise HTTPException(415, "엑셀(.xlsx) 파일을 올려 주세요.")
    too_big = HTTPException(413, f"파일은 {MAX_UPLOAD // 1_000_000}MB까지 올릴 수 있습니다.")
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_UPLOAD:
        raise too_big
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_UPLOAD:
            raise too_big
    if not data:
        raise HTTPException(422, "빈 파일입니다.")
    return bytes(data)


def parse_pos(data: bytes):
    from app.pos_import import PosFileError, parse

    try:
        return parse(data)
    except PosFileError as e:
        raise HTTPException(422, str(e)) from None


def file_name(request: Request) -> str | None:
    from urllib.parse import unquote

    name = unquote(request.headers.get("x-file-name", "")).strip()[:200]  # 한글 이름은 화면이 URL 인코딩해 보낸다
    return name or None


@app.get("/api/pos/sales", summary="한 달의 홀 매출: 일별 실매출, 메뉴별 매출, 두 파일 대조, 최근 올린 기록")
def pos_sales(s: DB, _: Owner, month: Month) -> dict:
    return reports.pos_sales(s, month)


@app.post("/api/pos/preview", summary="POS 엑셀 미리보기: 무엇이 들어가고 무엇이 바뀌는지 (DB는 그대로)")
def pos_preview(_: Owner, s: DB, data: Annotated[bytes, Depends(xlsx_body)]) -> dict:
    from app.pos_import import summary

    return summary(s, parse_pos(data))


@app.post("/api/pos/import", status_code=201, summary="POS 엑셀 넣기: 같은 기간 자료를 바꾼다")
def pos_import(user: Owner, s: DB, request: Request, data: Annotated[bytes, Depends(xlsx_body)]) -> dict:
    from app.pos_import import apply

    out = apply(s, parse_pos(data), user.id, file_name(request))
    s.commit()
    return out


class AskBody(BaseModel):
    question: Question


@lru_cache
def qa_agent():
    from app.qa.agent import Agent

    return Agent()


KST = timezone(timedelta(hours=9))


class DailyLimit:
    """하루 질문 수 제한 (한국 날짜 기준, 모든 사용자 합계). 공개 데모의 API 비용을 막는다.
    서버 1대·프로세스 1개 전제라 메모리에 센다. 서버를 다시 켜면 0부터 센다."""

    def __init__(self, limit: int | None):
        self.limit, self.day, self.used, self.lock = limit, None, 0, Lock()

    def take(self, today: date) -> int | None:
        """한 번 쓰고 남은 횟수를 돌려준다. 제한이 없으면 None, 다 썼으면 429."""
        if self.limit is None:
            return None
        with self.lock:
            if self.day != today:
                self.day, self.used = today, 0
            if self.used >= self.limit:
                raise HTTPException(429, f"오늘 질문 횟수({self.limit}회)를 다 썼어요. 내일 다시 써 보세요.")
            self.used += 1
            return self.limit - self.used

    def give_back(self, today: date) -> None:
        """답을 못 만들고 서버 쪽에서 실패한 질문은 횟수에서 뺀다."""
        with self.lock:
            if self.limit is not None and self.day == today and self.used > 0:
                self.used -= 1


def env_limit() -> int | None:
    v = os.environ.get("ASK_DAILY_LIMIT")
    return int(v) if v else None


ask_limit = DailyLimit(env_limit())


@app.post("/api/ask", summary="자연어 질의: 답변과 함께 실행한 SQL과 결과 표를 돌려준다")
def ask(body: AskBody, _: Owner) -> dict:
    today = datetime.now(KST).date()
    remaining = ask_limit.take(today)
    try:
        out = qa_agent().ask(body.question)
    except Exception:
        # Claude API 장애·키 없음 등. 사용자 잘못이 아니므로 횟수를 돌려준다
        ask_limit.give_back(today)
        log.exception("자연어 질의 실패")
        raise HTTPException(503, "AI 응답을 받지 못했습니다. 잠시 뒤 다시 해 보세요.") from None
    out.pop("usage", None)
    if remaining is not None:
        out["remaining_today"] = remaining
    return out


@app.get("/api/ask/quota", summary="오늘 남은 질문 수 (제한이 없으면 limit이 null)")
def ask_quota(_: Owner) -> dict:
    lim = ask_limit
    if lim.limit is None:
        return {"limit": None, "remaining": None}
    used = lim.used if lim.day == datetime.now(KST).date() else 0
    return {"limit": lim.limit, "remaining": max(0, lim.limit - used)}


@app.get("/api/sync-runs", summary="최근 동기화 기록")
def sync_runs(s: DB, _: Owner, limit: Annotated[int, Query(ge=1, le=100)] = 10) -> list[dict]:
    return reports.sync_runs(s, limit)

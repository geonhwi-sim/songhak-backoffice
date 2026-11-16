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

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints
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
FIELD_MESSAGES = {
    "question": f"질문은 2~{QUESTION_MAX}자로 입력해 주세요.",
    "email": "이메일을 확인해 주세요.",
    "password": f"비밀번호를 입력해 주세요 ({PASSWORD_MAX}자 이하).",
    "month": "월은 2026-09처럼 YYYY-MM 형식이어야 합니다.",
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

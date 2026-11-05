"""백오피스 API. 집계는 app/reports.py, 로그인·권한은 app/auth.py에 있다.

로컬 실행 (backend 폴더): uvicorn app.api:app --reload
문서: http://127.0.0.1:8000/docs

/api/health와 /api/auth/login을 빼면 모두 로그인이 필요하고, 조회 API는 사장님 계정만 쓸 수 있다.
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import text

from app import reports
from app.auth import COOKIE, Owner, User, authenticate, cookie_options, make_token
from app.deps import DB, get_session  # noqa: F401  테스트가 app.api.get_session을 바꿔 끼운다

app = FastAPI(title="송학구이 백오피스 API", version="0.2.0")

Month = Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM", examples=["2026-09"])]


@app.get("/api/health")
def health(s: DB) -> dict:
    s.execute(text("select 1"))
    return {"status": "ok"}


# ── 로그인 ──


class LoginBody(BaseModel):
    email: str
    password: str


def user_out(u) -> dict:
    return {"id": u.id, "email": u.email, "role": u.role}


@app.post("/api/auth/login", summary="로그인. 성공하면 HttpOnly 쿠키를 준다")
def login(body: LoginBody, s: DB, response: Response) -> dict:
    user = authenticate(s, body.email, body.password)
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


@app.get("/api/sync-runs", summary="최근 동기화 기록")
def sync_runs(s: DB, _: Owner, limit: Annotated[int, Query(ge=1, le=100)] = 10) -> list[dict]:
    return reports.sync_runs(s, limit)

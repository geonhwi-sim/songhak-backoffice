"""백오피스 조회 API. 집계는 app/reports.py에 있다.

로컬 실행 (backend 폴더): uvicorn app.api:app --reload
문서: http://127.0.0.1:8000/docs

아직 로그인이 없다 (3주차에 화면과 함께 정한다). 그 전에는 외부에 열지 않는다.
"""

from collections.abc import Iterator
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import reports
from app.db import SessionLocal

app = FastAPI(title="송학구이 백오피스 API", version="0.1.0")

Month = Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM", examples=["2026-09"])]


def get_session() -> Iterator[Session]:
    if SessionLocal is None:
        raise HTTPException(503, "DATABASE_URL이 설정되지 않았습니다")
    with SessionLocal() as s:
        yield s


DB = Annotated[Session, Depends(get_session)]


@app.get("/api/health")
def health(s: DB) -> dict:
    s.execute(text("select 1"))
    return {"status": "ok"}


@app.get("/api/home", summary="홈: 이번 급여 기간 인건비, 이번 달 정산, 최근 발주, 마지막 동기화")
def home(s: DB, day: date | None = None) -> dict:
    return reports.home(s, day)


@app.get("/api/settlements", summary="정산처별 월 정산 합계")
def settlements(s: DB, month: Month) -> dict:
    return reports.settlements(s, month)


@app.get("/api/settlements/{place_id}", summary="정산서: 한 정산처의 월 주문·항목 내역")
def settlement_detail(s: DB, place_id: int, month: Month) -> dict:
    detail = reports.settlement_detail(s, place_id, month)
    if detail is None:
        raise HTTPException(404, "정산처를 찾을 수 없습니다")
    return detail


@app.get("/api/payroll", summary="급여 기간(전월 17일 ~ 당월 16일)의 직원별 인건비")
def payroll(s: DB, month: Month) -> dict:
    return reports.payroll(s, month)


@app.get("/api/supply-orders", summary="발주 목록과 비용 (결제 금액 우선, 없으면 예상 금액)")
def supply_orders(
    s: DB,
    start: date | None = None,
    end: date | None = None,
    status: Literal["pending", "ordered", "done"] | None = None,
    include_deleted: bool = True,
) -> dict:
    if start and end and start > end:
        raise HTTPException(422, "start가 end보다 늦습니다")
    return reports.supply_orders(s, start, end, status, include_deleted)


@app.get("/api/sync-runs", summary="최근 동기화 기록")
def sync_runs(s: DB, limit: Annotated[int, Query(ge=1, le=100)] = 10) -> list[dict]:
    return reports.sync_runs(s, limit)

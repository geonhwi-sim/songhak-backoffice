"""자연어 질의 기반: 채점 규칙, 읽기 전용 계정, 평가 세트, 급여 기간 결과 테이블."""

import os
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import models as m
from app.db import engine
from app.payroll import pay_month_of, refresh_periods
from app.qa.db import Result
from app.qa.evaluate import load_questions, matches
from app.reports import payroll


def R(*rows):
    return Result(["c"] * (len(rows[0]) if rows else 0), list(rows), False)


# ── 채점 ──


def test_match_ignores_column_names_extra_columns_and_number_types():
    assert matches(R(("정산처A", 123400)), R((Decimal("123400.00"), "정산처A", 41)))
    assert matches(R((Decimal("83.5"),)), R((83.50,)))


def test_match_needs_same_rows():
    assert not matches(R(("a", 1), ("b", 2)), R(("a", 1)))
    assert not matches(R(("a", 1),), R(("a", 2),))


def test_match_order_only_when_asked():
    gold, got = R(("07", 1), ("08", 2)), R(("08", 2), ("07", 1))
    assert matches(gold, got)
    assert not matches(gold, got, ordered=True)


def test_no_data_means_empty_or_all_null():
    assert matches(R((None,)), R())
    assert not matches(R((None,)), R((0,)))


def test_eval_set_has_20_unique_questions():
    qs = load_questions()
    assert len(qs) == 20
    assert len({q.id for q in qs}) == 20
    assert all(q.kind in ("answer", "no_data") for q in qs)


# ── 급여 기간 결과 ──


@pytest.mark.parametrize("d, month", [(date(2026, 11, 16), "2026-11"), (date(2026, 11, 17), "2026-12"),
                                      (date(2026, 12, 20), "2027-01")])
def test_pay_month_of(d, month):
    assert pay_month_of(d) == month


needs_db = pytest.mark.skipif(engine is None, reason="DATABASE_URL 없음")


@pytest.fixture
def session():
    with engine.connect() as conn:
        tx = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        yield s
        s.close()
        tx.rollback()


@needs_db
def test_juhu_only_period_is_kept(session):
    """11/16(월) 15시간 근무 → 그 주 일요일(11/22)은 12월 급여 기간. 12월에 근무가 없어도 주휴수당이 남아야 한다."""
    emp = m.Employee(name="테스트 직원")
    session.add(emp)
    session.flush()
    session.add(m.EmployeeWageRate(employee_id=emp.id, hourly_rate=11000, valid_from=date(2026, 1, 1)))
    session.add(m.WorkLog(employee_id=emp.id, work_date=date(2026, 11, 16), start_time=time(6), end_time=time(21)))
    session.flush()

    refresh_periods(session)
    rows = {p.month: p for p in session.scalars(select(m.PayrollPeriod).where(m.PayrollPeriod.employee_id == emp.id))}
    assert rows["2026-11"].juhu_pay == 0 and rows["2026-11"].base_pay == 165000
    assert (rows["2026-12"].days, rows["2026-12"].juhu_pay) == (0, 33000)  # 15/40 × 8 × 11,000

    mine = [e for e in payroll(session, "2026-12")["employees"] if e["employee_id"] == emp.id]
    assert mine and mine[0]["juhu_pay"] == 33000


# ── 읽기 전용 계정 ──

needs_qa = pytest.mark.skipif(not os.environ.get("QA_DATABASE_URL") or engine is None,
                              reason="QA_DATABASE_URL 없음")


@needs_qa
@pytest.mark.parametrize("sql, error", [
    ("SELECT count(*) FROM public.sales_orders", "permission denied"),
    ("INSERT INTO qa.places(name) VALUES ('x')", "permission denied"),
    ("UPDATE qa.places SET name = 'x'", "permission denied"),  # 단순 뷰는 원래 수정 가능하므로 권한이 막아야 한다
    ("CREATE TEMP TABLE t(x int)", "read-only transaction"),
])
def test_qa_account_is_read_only(sql, error):
    from app.qa.db import run_sql

    with pytest.raises(DBAPIError, match=error):
        run_sql(sql)


@needs_qa
def test_qa_account_reads_views_and_caps_rows():
    from app.qa.db import MAX_ROWS, run_sql

    r = run_sql("SELECT generate_series(1, 500) AS n")
    assert (len(r.rows), r.truncated, r.columns) == (MAX_ROWS, True, ["n"])
    assert run_sql("SELECT count(*) FROM places").columns == ["count"]  # search_path = qa


@needs_qa
def test_every_gold_sql_runs_on_qa_account():
    from app.qa.db import run_sql

    for q in load_questions():
        run_sql(q.gold_sql)

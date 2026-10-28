"""인건비 계산 검증.

규칙 테스트는 가상 근무 기록으로 항상 돈다.
실데이터 테스트는 data/(gitignore)의 정산 앱 스냅샷과 기준표가 있을 때만 돈다.
"""

import csv
import json
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app.db import engine
from app.etl.rtdb import import_snapshot
from app.payroll import Log, Rate, calc_period_wage, load_employee, pay_period

RATE = [Rate(11000, date(2026, 1, 1))]


def shifts(*days: str, hours: int = 4) -> list[Log]:
    """10:00부터 hours시간씩 근무한 기록"""
    return [Log(date.fromisoformat(d), time(10), time(10 + hours)) for d in days]


def days_from(first: str, n: int) -> list[str]:
    d = date.fromisoformat(first)
    return [(d + timedelta(days=i)).isoformat() for i in range(n)]


# ── 규칙 ──


def test_pay_period_runs_from_17th_to_16th():
    assert pay_period("2026-11") == (date(2026, 10, 17), date(2026, 11, 16))
    assert pay_period("2027-01") == (date(2026, 12, 17), date(2027, 1, 16))


def test_base_pay_and_hours():
    logs = [Log(date(2026, 11, 2), time(10), time(14, 30), break_min=30)]
    w = calc_period_wage(logs, RATE, "2026-11")
    assert (w.days, w.hours, w.base_pay) == (1, Decimal(4), 44000)


def test_no_juhu_under_15_hours_and_capped_at_40():
    short = calc_period_wage(shifts(*days_from("2026-11-02", 3)), RATE, "2026-11")  # 12시간
    assert (short.juhu_weeks, short.juhu_pay) == (0, 0)
    long = calc_period_wage(shifts(*days_from("2026-11-02", 5), hours=9), RATE, "2026-11")  # 45시간
    assert long.juhu_pay == 8 * 11000


def test_week_across_boundary_goes_only_to_period_of_its_sunday():
    # 11/16(월)은 11월 기간, 11/17~19는 12월 기간. 그 주 일요일 11/22는 12월 기간
    logs = shifts("2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19")  # 16시간
    assert calc_period_wage(logs, RATE, "2026-11").juhu_weeks == 0
    assert calc_period_wage(logs, RATE, "2026-12").juhu_pay == round(16 / 40 * 8 * 11000)


def test_periods_until_oct_16_keep_old_rule_that_was_paid():
    # 9/14~20 주: 9월 기간(9/14~16)과 10월 기간(9/17~18)에 모두 근무 → 예전 방식대로 두 달 모두
    logs = shifts("2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18")  # 20시간
    assert calc_period_wage(logs, RATE, "2026-09").juhu_weeks == 1
    assert calc_period_wage(logs, RATE, "2026-10").juhu_weeks == 1


def test_week_paid_in_october_is_not_paid_again_in_november():
    # 10/12~18 주: 일요일 10/18은 11월 기간이지만 10/16 이전 근무가 있어 10월에 이미 지급
    logs = shifts(*days_from("2026-10-12", 5))
    assert calc_period_wage(logs, RATE, "2026-10").juhu_weeks == 1
    assert calc_period_wage(logs, RATE, "2026-11").juhu_weeks == 0


# ── 실데이터: 정산 앱 화면(지급 금액)과 대조 ──

DATA = Path(__file__).resolve().parents[2] / "data"
SNAPSHOTS = sorted(DATA.glob("rtdb_songhak_data_*.json"))
EXPECTED = DATA / "검증기준_인건비.csv"
HISTORY = DATA / "price_history.json"


def expected_rows() -> list[dict]:
    if not EXPECTED.exists():
        return []
    with EXPECTED.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


@pytest.mark.skipif(engine is None or not SNAPSHOTS or not EXPECTED.exists(), reason="DB 또는 실데이터 없음")
@pytest.mark.parametrize("row", expected_rows(), ids=lambda r: r["급여월"])
def test_matches_paid_wages(row):
    data = json.loads(SNAPSHOTS[-1].read_text(encoding="utf-8"))
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else {}
    with engine.connect() as conn:
        tx = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            import_snapshot(s, data, history)
            emp_id = s.scalar(select(m.Employee.id).where(m.Employee.name == row["직원"]))
            month = f"2026-{int(row['급여월'].removesuffix('월')):02d}"
            w = calc_period_wage(*load_employee(s, emp_id), month)
        finally:
            s.close()
            tx.rollback()

    assert (w.days, w.hours, w.base_pay) == (int(row["근무일"]), Decimal(row["근무시간"]), int(row["기본급"]))
    assert (w.juhu_weeks, w.juhu_pay, w.total) == (int(row["주휴_주수"]), int(row["주휴수당"]), int(row["합계"]))

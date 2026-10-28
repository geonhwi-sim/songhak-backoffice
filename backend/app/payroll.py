"""인건비 계산. 규칙은 docs/PRD.md 5.3절과 docs/decisions.md(2026-10-25 주휴수당 전환 규칙).

- 급여 기간: 전월 17일 ~ 당월 16일 ("2026-11"이면 10/17 ~ 11/16)
- 근무시간 = 종료 − 시작 − 휴게
- 기본급 = 근무시간 × 시급 (원 단위 반올림)
- 주휴수당: ISO 주(월~일) 근무가 15시간 이상이면 min(주 근무시간/40, 1) × 8 × 시급
  - 2026-10-17 이후 기간: 그 주의 일요일이 속한 기간에 한 번만 넣는다.
  - 2026-10-16까지 기간: 정산 앱 예전 방식(그 기간에 근무 기록이 있는 주를 모두 넣음)으로 이미 지급했으므로
    지급한 금액과 맞도록 예전 방식으로 계산한다. 그때 지급한 주는 이후 기간에서 뺀다.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m

JUHU_RULE_FROM = date(2026, 10, 17)
JUHU_MIN_HOURS = Decimal(15)


@dataclass(frozen=True)
class Log:
    work_date: date
    start: time
    end: time
    break_min: int = 0

    @property
    def hours(self) -> Decimal:
        minutes = (datetime.combine(self.work_date, self.end) - datetime.combine(self.work_date, self.start)).seconds // 60
        return Decimal(max(0, minutes - self.break_min)) / 60


@dataclass(frozen=True)
class Rate:
    hourly_rate: int
    valid_from: date
    valid_to: date | None = None  # 포함하지 않음 [from, to)


@dataclass(frozen=True)
class PeriodWage:
    start: date
    end: date
    days: int
    hours: Decimal
    base_pay: int
    juhu_weeks: int
    juhu_pay: int

    @property
    def total(self) -> int:
        return self.base_pay + self.juhu_pay


def won(x: Decimal) -> int:
    """원 단위 반올림 (정산 앱의 Math.round와 같음)."""
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def pay_period(month: str) -> tuple[date, date]:
    """'2026-11' → (2026-10-17, 2026-11-16)"""
    y, mo = map(int, month.split("-"))
    py, pm = (y - 1, 12) if mo == 1 else (y, mo - 1)
    return date(py, pm, 17), date(y, mo, 16)


def iso_week(d: date) -> tuple[int, int]:
    y, w, _ = d.isocalendar()
    return y, w


def week_sunday(d: date) -> date:
    return d + timedelta(days=7 - d.isoweekday())


def rate_at(rates: list[Rate], d: date) -> int:
    for r in rates:
        if r.valid_from <= d and (r.valid_to is None or d < r.valid_to):
            return r.hourly_rate
    raise ValueError(f"{d}에 적용할 시급이 없습니다")


def calc_period_wage(logs: list[Log], rates: list[Rate], month: str) -> PeriodWage:
    """한 직원의 근무 기록 전체와 시급 이력으로 month 급여 기간의 인건비를 계산한다.
    주휴수당은 기간 밖 근무까지 합친 주 단위 시간이 필요해서 전체 기록을 받는다."""
    start, end = pay_period(month)
    in_period = [lg for lg in logs if start <= lg.work_date <= end]

    hours = sum((lg.hours for lg in in_period), Decimal(0))
    base_pay = won(sum((lg.hours * rate_at(rates, lg.work_date) for lg in in_period), Decimal(0)))

    week_hours: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    week_last_day: dict[tuple[int, int], date] = {}
    for lg in logs:
        wk = iso_week(lg.work_date)
        week_hours[wk] += lg.hours
        week_last_day[wk] = max(week_last_day.get(wk, lg.work_date), lg.work_date)

    if end < JUHU_RULE_FROM:
        weeks = {iso_week(lg.work_date) for lg in in_period}
    else:
        paid = {iso_week(lg.work_date) for lg in logs if lg.work_date < JUHU_RULE_FROM}
        weeks = {iso_week(lg.work_date) for lg in logs if start <= week_sunday(lg.work_date) <= end} - paid

    juhu_weeks, juhu_pay = 0, 0
    for wk in weeks:
        h = week_hours[wk]
        if h >= JUHU_MIN_HOURS:
            juhu_weeks += 1
            juhu_pay += won(min(h / 40, Decimal(1)) * 8 * rate_at(rates, week_last_day[wk]))

    return PeriodWage(start, end, len({lg.work_date for lg in in_period}), hours, base_pay, juhu_weeks, juhu_pay)


def load_employee(session: Session, employee_id: int) -> tuple[list[Log], list[Rate]]:
    logs = [
        Log(w.work_date, w.start_time, w.end_time, w.break_min)
        for w in session.scalars(select(m.WorkLog).where(m.WorkLog.employee_id == employee_id))
    ]
    rates = [
        Rate(r.hourly_rate, r.valid_from, r.valid_to)
        for r in session.scalars(select(m.EmployeeWageRate).where(m.EmployeeWageRate.employee_id == employee_id))
    ]
    return logs, rates

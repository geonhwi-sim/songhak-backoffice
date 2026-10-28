"""대시보드 조회·집계. API(app/api.py)가 쓰고, 화면과 무관하게 테스트할 수 있게 분리한다.

- 정산 기간: 달력 기준 1일 ~ 말일. 원본에서 지워진 주문은 뺀다.
- 급여 기간: 전월 17일 ~ 당월 16일 (app/payroll.py)
- 발주 비용: 결제 금액이 있으면 그 값, 없으면 단가표 기준 예상 금액
- 날짜는 한국 시간 기준
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app import models as m
from app.payroll import calc_period_wage, load_employee, pay_period

KST = timezone(timedelta(hours=9))


def today_kst() -> date:
    return datetime.now(KST).date()


def month_range(month: str) -> tuple[date, date]:
    """'2026-09' → (2026-09-01, 2026-09-30)"""
    y, mo = map(int, month.split("-"))
    first = date(y, mo, 1)
    nxt = date(y + mo // 12, mo % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def current_pay_month(day: date) -> str:
    """그날이 속한 급여 기간의 이름. 17일부터는 다음 달 급여다."""
    if day.day >= 17:
        day = (day.replace(day=1) + timedelta(days=32)).replace(day=1)
    return f"{day.year}-{day.month:02d}"


# ── 정산 ──


def settlements(session: Session, month: str) -> dict:
    start, end = month_range(month)
    i, o, p = m.SalesOrderItem, m.SalesOrder, m.Place
    rows = session.execute(
        select(
            p.id, p.name,
            func.count(func.distinct(o.id)),
            func.coalesce(func.sum(i.amount), 0),
            func.coalesce(func.sum(i.qty * i.unit_price_normal), 0),
            func.count(i.id).filter(i.price_source == "estimated"),
            func.count(i.id).filter(i.needs_review),
        )
        .join(o, o.place_id == p.id).join(i, i.order_id == o.id)
        .where(o.order_date.between(start, end), o.source_deleted_at.is_(None))
        .group_by(p.id, p.name).order_by(p.id)
    ).all()
    places = [
        {"place_id": pid, "name": name, "orders": n, "amount": int(amount), "discount": int(normal - amount),
         "estimated_items": est, "review_items": rev}
        for pid, name, n, amount, normal, est, rev in rows
    ]
    return {"month": month, "start": start, "end": end, "places": places,
            "total": sum(x["amount"] for x in places)}


def settlement_detail(session: Session, place_id: int, month: str) -> dict | None:
    place = session.get(m.Place, place_id)
    if place is None:
        return None
    start, end = month_range(month)
    orders = session.scalars(
        select(m.SalesOrder).options(selectinload(m.SalesOrder.items))
        .where(m.SalesOrder.place_id == place_id, m.SalesOrder.order_date.between(start, end),
               m.SalesOrder.source_deleted_at.is_(None))
        .order_by(m.SalesOrder.order_date, m.SalesOrder.id)
    ).all()
    out = [
        {"order_id": o.id, "date": o.order_date,
         "items": [{"name": it.item_name, "option": it.option_name, "kind": it.kind, "qty": it.qty,
                    "unit_price": it.unit_price, "amount": it.amount, "is_discounted": it.is_discounted,
                    "price_source": it.price_source, "needs_review": it.needs_review} for it in o.items],
         "total": sum(it.amount for it in o.items)}
        for o in orders
    ]
    return {"place_id": place.id, "name": place.name, "month": month, "start": start, "end": end,
            "orders": out, "total": sum(x["total"] for x in out)}


# ── 인건비 ──


def payroll(session: Session, month: str) -> dict:
    start, end = pay_period(month)
    employees = []
    for e in session.scalars(select(m.Employee).order_by(m.Employee.id)):
        logs, rates = load_employee(session, e.id)
        if not any(start <= lg.work_date <= end for lg in logs):
            continue
        w = calc_period_wage(logs, rates, month)
        employees.append({"employee_id": e.id, "name": e.name, "days": w.days, "hours": float(w.hours),
                          "base_pay": w.base_pay, "juhu_weeks": w.juhu_weeks, "juhu_pay": w.juhu_pay,
                          "total": w.total})
    return {"month": month, "start": start, "end": end, "employees": employees,
            "total": sum(x["total"] for x in employees)}


# ── 발주 ──


def supply_orders(session: Session, start: date | None = None, end: date | None = None,
                  status: str | None = None, include_deleted: bool = True, limit: int | None = None) -> dict:
    o, pay = m.SupplyOrder, m.SupplyPayment
    q = (select(o).options(selectinload(o.items), selectinload(o.payment))
         .order_by(case((o.status == "pending", 0), else_=1), o.created_at.desc()))
    if start:
        q = q.where(o.created_at >= datetime.combine(start, datetime.min.time(), KST))
    if end:
        q = q.where(o.created_at < datetime.combine(end + timedelta(days=1), datetime.min.time(), KST))
    if status:
        q = q.where(o.status == status)
    if not include_deleted:
        q = q.where(o.source_deleted_at.is_(None))
    if limit:
        q = q.limit(limit)

    rows = []
    for x in session.scalars(q):
        paid = x.payment.paid_amount if x.payment else None
        rows.append({
            "id": x.id, "created_at": x.created_at, "requester": x.requester_name, "status": x.status,
            "memo": x.memo, "estimated_total": x.estimated_total, "paid_amount": paid,
            "cost": paid if paid is not None else (x.estimated_total or 0),
            "paid_on": x.payment.paid_on if x.payment else None,
            "card_label": x.payment.card_label if x.payment else None,
            "source_deleted": x.source_deleted_at is not None,
            "items": [{"name": it.item_name, "unit": it.unit, "quantity": float(it.quantity),
                       "unit_price": it.unit_price, "subtotal": it.subtotal} for it in x.items],
        })
    return {"orders": rows, "count": len(rows), "cost": sum(r["cost"] for r in rows)}


# ── 운영 ──


def sync_runs(session: Session, limit: int = 10) -> list[dict]:
    runs = session.scalars(select(m.SyncRun).order_by(m.SyncRun.id.desc()).limit(limit))
    return [{"id": r.id, "source": r.source, "status": r.status, "started_at": r.started_at,
             "finished_at": r.finished_at, "stats": r.stats, "error": r.error} for r in runs]


def home(session: Session, day: date | None = None) -> dict:
    day = day or today_kst()
    last = {}
    for r in sync_runs(session, limit=20):
        last.setdefault(r["source"], r)
    return {
        "today": day,
        "payroll": payroll(session, current_pay_month(day)),
        "settlements": settlements(session, f"{day.year}-{day.month:02d}"),
        "supply_orders": supply_orders(session, include_deleted=False, limit=10),
        "last_sync": last,
    }

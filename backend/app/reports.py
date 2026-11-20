"""대시보드 조회·집계. API(app/api.py)가 쓰고, 화면과 무관하게 테스트할 수 있게 분리한다.

- 정산 기간: 달력 기준 1일 ~ 말일. 원본에서 지워진 주문은 뺀다.
- 급여 기간: 전월 17일 ~ 당월 16일 (app/payroll.py)
- 발주 비용: 결제 금액이 있으면 그 값, 없으면 단가표 기준 예상 금액
- 지출: 백오피스에서 직접 입력한 돈 (매장·도매상 장보기, 공과금). 손익 = 매출 − (지출 + 발주 + 인건비)
- 홀 매출: OK POS 엑셀에서 올린 일별 실매출 (app/pos_import.py). 매출 = POS − POS에 찍은 외상 + 외상 정산
- 날짜는 한국 시간 기준
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app import models as m
from app.payroll import calc_period_wage, load_employee, pay_period, rate_at, won

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
        if not logs:
            continue
        w = calc_period_wage(logs, rates, month)
        if w.total == 0:  # 근무가 없어도 지난 주의 주휴수당만 있을 수 있다
            continue
        employees.append({"employee_id": e.id, "name": e.name, "days": w.days, "hours": float(w.hours),
                          "base_pay": w.base_pay, "juhu_weeks": w.juhu_weeks, "juhu_pay": w.juhu_pay,
                          "total": w.total})
    return {"month": month, "start": start, "end": end, "employees": employees,
            "total": sum(x["total"] for x in employees)}


def my_pay(session: Session, employee_id: int, month: str, today: date | None = None) -> dict | None:
    """직원 본인 화면: 한 급여 기간의 근무 기록과 (예상) 급여. 사장님 화면과 같은 계산(calc_period_wage)을 쓴다."""
    emp = session.get(m.Employee, employee_id)
    if emp is None:
        return None
    today = today or today_kst()
    start, end = pay_period(month)
    logs, rates = load_employee(session, employee_id)
    w = calc_period_wage(logs, rates, month)
    in_period = sorted((lg for lg in logs if start <= lg.work_date <= end), key=lambda lg: (lg.work_date, lg.start))
    rows = []
    for lg in in_period:
        rate = rate_at(rates, lg.work_date)
        rows.append({"date": lg.work_date, "start": lg.start.strftime("%H:%M"), "end": lg.end.strftime("%H:%M"),
                     "break_min": lg.break_min, "hours": float(lg.hours), "hourly_rate": rate,
                     "pay": won(lg.hours * rate)})
    return {"name": emp.name, "month": month, "start": start, "end": end,
            # 기간이 끝나지 않았으면 앞으로의 근무와 주휴수당이 더해질 수 있다
            "is_estimate": today <= end,
            "days": w.days, "hours": float(w.hours), "base_pay": w.base_pay, "juhu_weeks": w.juhu_weeks,
            "juhu_pay": w.juhu_pay, "total": w.total, "logs": rows}


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


# ── 지출 · 손익 ──


def expenses(session: Session, month: str) -> dict:
    """한 달(달력 기준)의 지출. 지운 건은 뺀다."""
    start, end = month_range(month)
    e = m.Expense
    rows = session.scalars(
        select(e).where(e.spent_on.between(start, end), e.deleted_at.is_(None)).order_by(e.spent_on.desc(), e.id.desc())
    ).all()
    by_category = {c: 0 for c in m.EXPENSE_CATEGORIES}
    for x in rows:
        by_category[x.category] += x.amount
    return {
        "month": month, "start": start, "end": end,
        "expenses": [{"id": x.id, "spent_on": x.spent_on, "vendor": x.vendor, "category": x.category,
                      "amount": x.amount, "memo": x.memo, "source": x.source} for x in rows],
        "by_category": by_category, "total": sum(by_category.values()),
    }


def pos_sales(session: Session, month: str) -> dict:
    """한 달(달력 기준)의 홀 매출 (OK POS 엑셀에서 올린 것).

    - 일별: 영업일마다 포스를 합친 실매출. 올린 날까지만 있다.
    - 메뉴별: 기간이 그 달 안에 있는 상품 분석 자료를 메뉴(상품코드)별로 더한다. 기간은 겹치지 않게 저장된다.
    - 대조: 메뉴 자료의 기간마다 일별 실매출 합과 메뉴 실매출 합이 같은지 본다 (두 파일이 같은 POS 숫자인지).
    - credit_item: 외상 거래처 주문을 찍은 '주문확인용' 품목 금액. 정산 앱의 외상과 같은 돈이다.
    """
    from app.pos_import import CREDIT_ITEMS

    start, end = month_range(month)
    d = m.PosDailySale
    days = [
        {"date": day, "net": int(net), "gross": int(gross), "discount": int(disc), "cash": int(cash)}
        for day, net, gross, disc, cash in session.execute(
            select(d.business_date, func.sum(d.net), func.sum(d.gross), func.sum(d.discount), func.sum(d.cash))
            .where(d.business_date.between(start, end)).group_by(d.business_date).order_by(d.business_date))
    ]

    t = m.PosMenuSale
    in_month = (t.period_start >= start, t.period_end <= end)
    periods = session.execute(
        select(t.period_start, t.period_end, func.sum(t.net)).where(*in_month)
        .group_by(t.period_start, t.period_end).order_by(t.period_start)).all()
    items: dict[str, dict] = {}
    for x in session.scalars(select(t).where(*in_month).order_by(t.period_start)):
        it = items.setdefault(x.code, {"code": x.code, "name": x.name, "category": x.category, "net": 0, "qty": 0})
        it["net"] += x.net
        it["qty"] += x.qty
        it["name"], it["category"] = x.name, x.category  # 이름이 바뀌었으면 나중 기간 이름으로
    menu_items = sorted(items.values(), key=lambda x: (-x["net"], -x["qty"], x["code"]))
    for x in menu_items:
        x["is_credit"] = x["name"] in CREDIT_ITEMS

    checks = []
    for a, b, menu_net in periods:
        daily_net = sum(x["net"] for x in days if a <= x["date"] <= b)
        checks.append({"start": a, "end": b, "menu_net": int(menu_net), "daily_net": daily_net,
                       "match": int(menu_net) == daily_net})

    imp = m.PosImport
    last = session.scalars(select(imp).order_by(imp.id.desc()).limit(5)).all()
    return {
        "month": month, "start": start, "end": end,
        "days": days, "day_count": len(days), "net_total": sum(x["net"] for x in days),
        "cash_total": sum(x["cash"] for x in days), "last_date": days[-1]["date"] if days else None,
        "menu": menu_items, "menu_net_total": sum(x["net"] for x in menu_items),
        "menu_periods": [{"start": a, "end": b} for a, b, _ in periods],
        "menu_until": periods[-1][1] if periods else None,
        "credit_item": sum(x["net"] for x in menu_items if x["is_credit"]),
        "checks": checks,
        "imports": [{"id": x.id, "kind": x.kind, "start": x.period_start, "end": x.period_end,
                     "file_name": x.file_name, "rows": x.rows, "net_total": x.net_total,
                     "imported_at": x.imported_at} for x in last],
    }


def profit(session: Session, month: str) -> dict:
    """월 손익 요약 (달력 기준 한 달).

    - 매출: 홀 매출(POS 실매출) − POS에 찍은 외상(주문확인용) + 외상 정산(정산처 식대).
      외상 거래처 주문은 POS에도 '주문확인용'으로 찍고 정산 앱에도 남아서, 그냥 더하면 두 번 센다.
      메뉴별 자료(상품 분석)를 올린 기간만 뺄 수 있으므로, 일별 자료가 메뉴 자료보다 뒤까지 있으면
      credit_unchecked로 알린다.
    - 비용: 직접 입력한 지출 + 발주 앱 비용 + 인건비.
      인건비는 그 달 16일에 끝나는 급여 기간(전월 17일 ~ 당월 16일)의 금액이다. 그 달에 나가는 돈이라서다.
    - 발주 앱에 결제를 남긴 건을 지출에도 넣으면 두 번 세어진다 (화면에서 안내한다).
    """
    credit = settlements(session, month)["total"]
    exp = expenses(session, month)
    start, end = month_range(month)
    supply = supply_orders(session, start, end)["cost"]
    labor = payroll(session, month)
    pay_start, pay_end = pay_period(month)
    pos = pos_sales(session, month)
    connected = pos["day_count"] > 0

    revenue = {
        "credit": credit,
        "pos": pos["net_total"] if connected else None,
        "pos_credit": pos["credit_item"] if connected and pos["menu_until"] else None,  # 뺄 금액 (양수)
    }
    costs = {**exp["by_category"], "supply_app": supply, "labor": labor["total"]}
    revenue_total = credit + (revenue["pos"] or 0) - (revenue["pos_credit"] or 0)
    cost_total = sum(costs.values())
    return {
        "month": month, "start": start, "end": end, "pay_start": pay_start, "pay_end": pay_end,
        "revenue": revenue, "revenue_total": revenue_total,
        "costs": costs, "cost_total": cost_total,
        "profit": revenue_total - cost_total,
        "expense_count": len(exp["expenses"]),
        "pos_connected": connected,
        "pos_days": pos["day_count"], "pos_last_date": pos["last_date"], "pos_menu_until": pos["menu_until"],
        "credit_unchecked": connected and (pos["menu_until"] is None or pos["menu_until"] < pos["last_date"]),
    }


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
        # 발주 앱은 7일 지난 발주를 지운다. 지워진 것도 실제 발주이므로 함께 보여 준다
        "supply_orders": supply_orders(session, limit=10),
        "last_sync": last,
    }

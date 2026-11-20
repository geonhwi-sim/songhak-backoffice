"""정산 앱(RTDB) 스냅샷을 Postgres로 가져온다.

실행 (backend 폴더에서):
    python -m app.etl.rtdb ../data/rtdb_songhak_data_2026-10-19.json

규칙은 docs/erd.md "가져오기 규칙"을 따른다.
- 원본 ID(source_id) 기준 upsert라 여러 번 돌려도 중복이 생기지 않는다.
- 이력 테이블(가격·할인·시급)은 원본의 현재 값과 data/price_history.json으로 매번 다시 만든다.
  지금은 이 스크립트만 이력을 쓰기 때문이다. 백오피스에서 가격을 고치는 기능이 생기면 바꾼다.
- 주문 항목은 원본에 ID가 없어서 주문마다 지우고 다시 넣는다.
- 원본에서 사라진 주문은 지우지 않고 source_deleted_at을 찍는다.
- 전체를 트랜잭션 하나로 처리한다. 실행 기록(sync_runs)만 별도 트랜잭션이라 실패해도 남는다.
"""

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import models as m
from app.etl.common import run_recorded, upsert
from app.payroll import refresh_periods

REPO = Path(__file__).resolve().parents[3]
DEFAULT_HISTORY = REPO / "data" / "price_history.json"
# 이력의 첫 구간 시작일. 정산 앱 기록(근무 6/17, 주문 7/1)보다 앞이면 된다.
START = date(2026, 6, 1)


def to_list(x) -> list:
    """RTDB는 빈 배열을 null로, 배열을 {"0": ...} 객체로 돌려줄 때가 있다 (정산 앱 toArr와 같은 처리)."""
    if not x:
        return []
    return [v for v in (x.values() if isinstance(x, dict) else x) if v]


def to_date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def disc(price: int, rate: Decimal) -> int:
    """할인가. 정산 앱의 Math.round(price * (1 - rate/100))와 같은 값 (원 단위 반올림)."""
    return int((Decimal(price) * (100 - rate) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# ── 가격 기준표 (DB에 넣을 이력이자, 주문 단가를 정하는 근거) ──


@dataclass(frozen=True)
class Period:
    value: object
    valid_from: date
    valid_to: date | None = None  # 이 날짜는 포함하지 않는다 [from, to)
    estimated: bool = False  # 사장님 기억 등 확인되지 않은 이력에서 온 구간

    def covers(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day < self.valid_to)


def period_at(periods: list[Period], day: date) -> Period | None:
    return next((p for p in periods if p.covers(day)), None)


@dataclass
class Catalog:
    categories: list[dict] = field(default_factory=list)
    menus: dict[str, dict] = field(default_factory=dict)  # source_id → name, category, active
    options: dict[str, dict] = field(default_factory=dict)  # source_id → menu, name, active
    deleted_menus: dict[str, dict] = field(default_factory=dict)  # 직접 입력 항목으로 바꿀 지운 메뉴
    missing_items: dict[str, list[dict]] = field(default_factory=dict)  # 주문 source_id → 앱에서 빠진 항목
    menu_prices: dict[str, list[Period]] = field(default_factory=dict)
    option_prices: dict[str, list[Period]] = field(default_factory=dict)
    place_pricing: dict[tuple[str, str], list[Period]] = field(default_factory=dict)  # (정산처, 메뉴)
    discount_rates: list[Period] = field(default_factory=list)


def build_catalog(data: dict, history: dict) -> Catalog:
    cat = Catalog()
    cat.categories = to_list(data.get("categories"))
    cat.discount_rates = [Period(Decimal(data.get("discountRate") or 10), START)]

    old_prices = {h["source_menu_id"]: h for h in history.get("menu_prices", [])}
    for mn in to_list(data.get("menus")):
        cat.menus[mn["id"]] = {"name": mn["name"], "category": mn.get("categoryId"), "active": True}
        # 옛 가격: data/price_history.json(사장님 기억, 추정) + 정산 앱이 남긴 history [{price, to}] (2026-11-20~)
        # to는 그 가격의 마지막 날 다음 날이다 [from, to). 판매 중지(hidden)는 가격과 무관해서 보지 않는다
        cuts = [(to_date(h["to"]), int(h["price"]), False) for h in to_list(mn.get("history"))]
        old = old_prices.get(mn["id"])
        if old:
            cuts.append((to_date(old["valid_to"]), old["price"], True))
        periods, start = [], START
        for cut, price, estimated in sorted(cuts):
            if cut > start:
                periods.append(Period(price, start, cut, estimated))
                start = cut
        cat.menu_prices[mn["id"]] = periods + [Period(mn["price"], start)]
        for op in to_list(mn.get("options")):
            cat.options[op["id"]] = {"menu": mn["id"], "name": op["name"], "active": True}
            cat.option_prices[op["id"]] = [Period(op["extraPrice"], START)]

    # 정산 앱에서 지운 옵션·메뉴: 사장님이 알려 준 값으로 비활성 행을 복원한다
    for op in history.get("deleted_options", []):
        sid = op["source_option_id"]
        cat.options[sid] = {"menu": op["menu"], "name": op["name"], "active": False}
        cat.option_prices[sid] = [
            Period(op["extra_price"], to_date(op.get("valid_from")) or START, to_date(op.get("valid_to")), True)
        ]
    for mn in history.get("deleted_menus", []):
        cat.deleted_menus[mn["source_menu_id"]] = {"name": mn["name"], "price": mn["price"]}
    # 앱 기록에서 빠졌지만 실제로 청구한 항목 (통장 결제액 대조로 확인). 해당 주문에 메뉴 줄로 더한다
    for it in history.get("missing_items", []):
        cat.missing_items.setdefault(it["order_id"], []).append({"menuId": it["menu"], "qty": it["qty"]})

    # 이력에도 없는 지운 메뉴·옵션은 0원짜리 비활성 행으로 두고 검토 대상으로 남긴다
    for order in to_list(data.get("orders")):
        for it in to_list(order.get("items")):
            if it.get("custom"):
                continue
            mid, oid = it["menuId"], it.get("optionId")
            if mid not in cat.menus and mid not in cat.deleted_menus:
                cat.menus[mid] = {"name": "(삭제된 메뉴)", "category": None, "active": False}
                cat.menu_prices[mid] = [Period(0, START, None, True)]
            if oid and oid not in cat.options:
                cat.options[oid] = {"menu": mid, "name": "(삭제된 옵션)", "active": False}
                cat.option_prices[oid] = [Period(0, START, None, True)]

    old_pricing = {h["source_place_id"]: h for h in history.get("place_menu_pricing", [])}
    for pl in to_list(data.get("places")):
        current = pl.get("menuPriceType") or {}
        old = old_pricing.get(pl["id"])
        cut = START
        if old:
            cut = to_date(old["valid_to"])
            for mid, menu in cat.menus.items():
                if menu["active"]:
                    kind = "normal" if mid in old["discount_all_except"] else "discount"
                    cat.place_pricing[(pl["id"], mid)] = [Period(kind, START, cut, True)]
        for mid, kind in current.items():
            if mid in cat.menus:  # 지운 메뉴를 가리키는 설정은 버린다
                cat.place_pricing.setdefault((pl["id"], mid), []).append(Period(kind, cut))
    return cat


# ── 주문 항목 변환 ──


@dataclass
class Line:
    kind: str
    menu: str | None
    option: str | None
    item_name: str
    option_name: str | None
    qty: int
    unit_price_normal: int
    unit_price: int
    is_discounted: bool
    price_source: str
    needs_review: bool

    @property
    def amount(self) -> int:
        return self.qty * self.unit_price


def merge_legacy_options(items: list[dict], cat: Catalog) -> list[dict]:
    """예전 방식 옵션(추가금만)을 정산 앱 수정 화면(toFullOptions)과 같은 방식으로 "옵션 붙은 1그릇"으로 합친다.
    기본 메뉴 수량이 옵션보다 적으면 그대로 둔다. 합계는 바뀌지 않는다."""
    items = [dict(it) for it in items]
    for opt in items:
        oid = opt.get("optionId")
        if not oid or opt.get("full") or opt.get("custom") or cat.options[oid]["name"] == "(삭제된 옵션)":
            continue
        base = next(
            (b for b in items if not b.get("custom") and not b.get("optionId") and b["menuId"] == opt["menuId"]),
            None,
        )
        if base is None or base["qty"] < opt["qty"]:
            continue
        base["qty"] -= opt["qty"]
        opt["full"] = True
    return [it for it in items if it["qty"] > 0]


def convert_items(order: dict, cat: Catalog, merge: bool = True) -> list[Line]:
    day, place = to_date(order["date"]), order["placeId"]
    rate = period_at(cat.discount_rates, day).value
    items = to_list(order.get("items")) + cat.missing_items.get(order["id"], [])
    if merge:
        items = merge_legacy_options(items, cat)

    lines = []
    for it in items:
        qty = int(it["qty"])
        if it.get("custom"):
            price, on = int(it.get("price") or 0), bool(it.get("discount"))
            lines.append(Line("custom", None, None, it.get("name") or "직접 입력", None, qty,
                              price, disc(price, rate) if on else price, on, "history", False))
            continue

        mid, oid = it["menuId"], it.get("optionId")
        if mid in cat.deleted_menus:  # 메뉴로 입력했던 직접 입력 금액
            dm = cat.deleted_menus[mid]
            lines.append(Line("custom", None, None, dm["name"], None, qty,
                              dm["price"], dm["price"], False, "estimated", True))
            continue

        menu = cat.menus[mid]
        base = period_at(cat.menu_prices[mid], day)
        pricing = period_at(cat.place_pricing.get((place, mid), []), day)
        if base is None:
            raise ValueError(f"{order['id']}: {day}에 메뉴 {mid}의 가격이 없습니다")
        on = pricing is not None and pricing.value == "discount"
        d = (lambda p: disc(p, rate)) if on else (lambda p: p)
        used = [base] + ([pricing] if pricing else [])
        review = not menu["active"]

        if not oid:
            kind, option_name, normal, unit = "menu", None, base.value, d(base.value)
        else:
            opt, extra = cat.options[oid], period_at(cat.option_prices[oid], day)
            if extra is None:
                raise ValueError(f"{order['id']}: {day}에 옵션 {oid}의 추가금이 없습니다")
            used.append(extra)
            review = review or not opt["active"]
            option_name = opt["name"]
            if it.get("full"):  # 옵션 붙은 1그릇: 기본가와 추가금을 각각 할인
                kind, normal, unit = "option", base.value + extra.value, d(base.value) + d(extra.value)
            else:
                kind, normal, unit = "legacy_option", extra.value, d(extra.value)

        source = "estimated" if review or any(p.estimated for p in used) else "history"
        lines.append(Line(kind, mid, oid, menu["name"], option_name, qty, normal, unit, on, source, review))
    return lines


# ── DB 쓰기 ──


def period_rows(periods: list[Period], column: str, **fixed) -> list[dict]:
    """이력 구간을 DB 행으로. column은 값이 들어갈 컬럼 이름."""
    return [{**fixed, column: p.value, "valid_from": p.valid_from, "valid_to": p.valid_to} for p in periods]


def import_snapshot(session: Session, data: dict, history: dict) -> dict:
    cat = build_catalog(data, history)
    now = datetime.now(timezone.utc)

    # 정산처·메뉴
    cat_ids = upsert(session, m.MenuCategory, [
        {"source_id": c["id"], "name": c["name"], "sort_order": i} for i, c in enumerate(cat.categories)
    ])
    places = to_list(data.get("places"))
    place_ids = upsert(session, m.Place, [{"source_id": p["id"], "name": p["name"], "is_active": True} for p in places])
    session.execute(update(m.Place).where(m.Place.source_id.not_in(list(place_ids))).values(is_active=False))
    menu_ids = upsert(session, m.Menu, [
        {"source_id": sid, "name": mn["name"], "category_id": cat_ids.get(mn["category"]), "is_active": mn["active"]}
        for sid, mn in cat.menus.items()
    ])
    option_ids = upsert(session, m.MenuOption, [
        {"source_id": sid, "menu_id": menu_ids[op["menu"]], "name": op["name"], "is_active": op["active"]}
        for sid, op in cat.options.items()
    ])

    # 이력 테이블: 매번 다시 만든다
    for model in (m.MenuPrice, m.MenuOptionPrice, m.PlaceMenuPricing, m.DiscountRate, m.EmployeeWageRate):
        session.execute(delete(model))
    session.execute(insert(m.MenuPrice), [
        r for sid, ps in cat.menu_prices.items() for r in period_rows(ps, "price", menu_id=menu_ids[sid])
    ])
    session.execute(insert(m.MenuOptionPrice), [
        r for sid, ps in cat.option_prices.items() for r in period_rows(ps, "extra_price", option_id=option_ids[sid])
    ])
    rows = [
        r for (pl, mid), ps in cat.place_pricing.items()
        for r in period_rows(ps, "price_type", place_id=place_ids[pl], menu_id=menu_ids[mid])
    ]
    if rows:
        session.execute(insert(m.PlaceMenuPricing), rows)
    session.execute(insert(m.DiscountRate), period_rows(cat.discount_rates, "rate_percent"))

    # 주문
    orders = to_list(data.get("orders"))
    order_ids = upsert(session, m.SalesOrder, [
        {"source_id": o["id"], "place_id": place_ids[o["placeId"]], "order_date": to_date(o["date"]),
         "source_deleted_at": None}
        for o in orders
    ])
    session.execute(delete(m.SalesOrderItem).where(m.SalesOrderItem.order_id.in_(list(order_ids.values()))))
    item_rows, review = [], 0
    for o in orders:
        for no, ln in enumerate(convert_items(o, cat), start=1):
            review += ln.needs_review
            item_rows.append({
                "order_id": order_ids[o["id"]], "line_no": no, "kind": ln.kind,
                "menu_id": menu_ids.get(ln.menu), "option_id": option_ids.get(ln.option),
                "item_name": ln.item_name, "option_name": ln.option_name, "qty": ln.qty,
                "unit_price_normal": ln.unit_price_normal, "unit_price": ln.unit_price,
                "is_discounted": ln.is_discounted, "price_source": ln.price_source, "needs_review": ln.needs_review,
            })
    session.execute(insert(m.SalesOrderItem), item_rows)
    gone = session.execute(
        update(m.SalesOrder)
        .where(m.SalesOrder.source_id.is_not(None), m.SalesOrder.source_id.not_in(list(order_ids)),
               m.SalesOrder.source_deleted_at.is_(None))
        .values(source_deleted_at=now)
    ).rowcount

    # 인건비
    employees = to_list(data.get("employees"))
    emp_ids = upsert(session, m.Employee, [{"source_id": e["id"], "name": e["name"], "is_active": True} for e in employees])
    session.execute(insert(m.EmployeeWageRate), [
        {"employee_id": emp_ids[e["id"]], "hourly_rate": int(e.get("hourlyRate") or data.get("wageRate")),
         "valid_from": START, "valid_to": None}
        for e in employees
    ])
    logs = to_list(data.get("workLogs"))
    upsert(session, m.WorkLog, [
        {"source_id": w["id"], "employee_id": emp_ids[w["employeeId"]], "work_date": to_date(w["date"]),
         "start_time": time.fromisoformat(w["startTime"]), "end_time": time.fromisoformat(w["endTime"]),
         "break_min": int(w.get("breakMin") or 0)}
        for w in logs
    ])
    memos = data.get("memos") or {}
    upsert(session, m.DayMemo, [{"memo_date": to_date(k), "body": v} for k, v in memos.items() if v], key="memo_date")

    session.flush()
    periods = refresh_periods(session)  # 자연어 질의가 읽는 급여 기간별 결과
    return {
        "places": len(place_ids), "menus": len(menu_ids), "options": len(option_ids),
        "orders": len(order_ids), "orders_marked_deleted": gone,
        "source_items": sum(len(to_list(o.get("items"))) for o in orders), "items": len(item_rows),
        "items_needs_review": review, "employees": len(emp_ids), "work_logs": len(logs), "memos": len(memos),
        "payroll_periods": periods,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="정산 앱(RTDB) 스냅샷을 Postgres로 가져온다")
    ap.add_argument("snapshot", type=Path, help="RTDB에서 내보낸 JSON 파일")
    ap.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="과거 가격 이력 JSON")
    args = ap.parse_args(argv)
    data = json.loads(args.snapshot.read_text(encoding="utf-8"))
    history = json.loads(args.history.read_text(encoding="utf-8")) if args.history.exists() else {}
    run_recorded("rtdb", lambda s: import_snapshot(s, data, history))


if __name__ == "__main__":
    main()

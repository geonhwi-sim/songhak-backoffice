"""발주 앱(Firestore)에서 내려받은 JSON을 Postgres로 가져온다.

내려받기 (저장소 루트):  node tools/export_firestore.js
가져오기 (backend 폴더): python -m app.etl.firestore ../data            # 가장 최근 날짜
                         python -m app.etl.firestore ../data --date 2026-10-28

입력은 Firestore REST 응답 형식({"documents": [...]})이다. 규칙은 docs/erd.md "발주" 절.
- 발주는 7일 뒤 원본(orders)에서 지워지므로 보관본(ordersArchive)을 기준으로 하고,
  아직 보관되지 않은 orders가 있으면 더한다. 원본에서 지워진 발주는 source_deleted_at을 찍는다.
- 발주 항목에는 품목 ID가 없다. 당시 이름·단위·가격을 저장하고, 이름이 같은 품목이 있으면 연결한다.
- 품목·요청인·카드가 원본에서 사라지면 지우지 않고 비활성으로 둔다.
- 결제 정보는 관리자 전용 컬렉션(orderPayments)에서 온다. 카드를 지워도 card_label로 이름이 남는다.
- 품목 사진(base64)은 가져오지 않는다.
"""

import argparse
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import models as m
from app.etl.common import run_recorded, upsert

COLLECTIONS = ("categories", "items", "requesters", "orders", "ordersArchive", "orderPayments", "paymentCards")


def decode(v: dict):
    """Firestore REST의 타입 붙은 값 → 파이썬 값"""
    kind, x = next(iter(v.items()))
    if kind == "mapValue":
        return {k: decode(y) for k, y in (x.get("fields") or {}).items()}
    if kind == "arrayValue":
        return [decode(y) for y in x.get("values") or []]
    if kind == "integerValue":
        return int(x)
    if kind == "timestampValue":
        return datetime.fromisoformat(x.replace("Z", "+00:00"))
    if kind == "nullValue":
        return None
    return x  # stringValue, booleanValue, doubleValue


def documents(raw: dict | None) -> dict[str, dict]:
    """{"documents": [...]} → {문서 ID: 필드 dict}"""
    return {
        d["name"].rsplit("/", 1)[-1]: {k: decode(v) for k, v in (d.get("fields") or {}).items()}
        for d in (raw or {}).get("documents", [])
    }


def load_export(folder: Path, day: str | None = None) -> dict[str, dict[str, dict]]:
    """data/firestore_<컬렉션>_<날짜>.json을 읽는다. 날짜를 안 주면 보관본이 있는 가장 최근 날짜."""
    if day is None:
        found = sorted(folder.glob("firestore_ordersArchive_*.json"))
        if not found:
            raise SystemExit(f"{folder}에 firestore_ordersArchive_*.json이 없습니다. tools/export_firestore.js로 내려받으세요.")
        day = found[-1].stem.rsplit("_", 1)[-1]
    out = {}
    for col in COLLECTIONS:
        f = folder / f"firestore_{col}_{day}.json"
        out[col] = documents(json.loads(f.read_text(encoding="utf-8"))) if f.exists() else {}
    return out


def import_export(session: Session, data: dict[str, dict[str, dict]]) -> dict:
    now = datetime.now(timezone.utc)

    def deactivate(model, seen) -> None:
        session.execute(update(model).where(model.source_id.is_not(None), model.source_id.not_in(list(seen)))
                        .values(is_active=False))

    # 카테고리·품목·요청인·카드
    cats = data["categories"]
    cat_ids = upsert(session, m.SupplyCategory, [
        {"source_id": sid, "name": c["name"], "sort_order": c.get("order", 0)} for sid, c in cats.items()
    ])
    cat_by_name = {c["name"]: cat_ids[sid] for sid, c in cats.items()}

    items = data["items"]
    item_ids = upsert(session, m.SupplyItem, [
        {"source_id": sid, "category_id": cat_by_name.get(it.get("category")), "name": it["name"],
         "unit": it.get("unit") or None, "ref_price": it.get("price"),
         "purchase_url": it.get("purchaseUrl") or None, "note": it.get("note") or None, "is_active": True}
        for sid, it in items.items()
    ])
    deactivate(m.SupplyItem, item_ids)
    item_by_name: dict[str, int] = {}
    for sid, it in sorted(items.items()):  # 이름이 겹치면 처음 것
        item_by_name.setdefault(it["name"], item_ids[sid])

    reqs = data["requesters"]
    req_ids = upsert(session, m.Requester, [
        {"source_id": sid, "name": r["name"], "sort_order": r.get("order", 0), "is_active": True}
        for sid, r in reqs.items()
    ])
    deactivate(m.Requester, req_ids)
    req_by_name = {r["name"]: req_ids[sid] for sid, r in reqs.items()}

    cards = data["paymentCards"]
    card_ids = upsert(session, m.PaymentCard, [
        {"source_id": sid, "nickname": c["nickname"], "last4": c["last4"], "is_active": c.get("isActive") is not False}
        for sid, c in cards.items()
    ])
    deactivate(m.PaymentCard, card_ids)

    # 발주: 보관본 + 아직 보관되지 않은 원본. 원본이 있으면 최신 상태로 본다
    orders = dict(data["ordersArchive"])
    for sid, o in data["orders"].items():
        orders[sid] = {**o, "sourceDeletedAt": None}

    def created(o: dict) -> datetime:
        if isinstance(o.get("createdAt"), datetime):
            return o["createdAt"]
        return datetime.fromtimestamp(o["createdAtMs"] / 1000, timezone.utc)

    order_ids = upsert(session, m.SupplyOrder, [
        {"source_id": sid, "requester_id": req_by_name.get(o.get("requester")), "requester_name": o.get("requester") or "",
         "status": o.get("status") or "pending", "memo": o.get("memo") or None, "estimated_total": o.get("total"),
         "created_at": created(o), "source_deleted_at": o.get("sourceDeletedAt")}
        for sid, o in orders.items()
    ])
    gone = session.execute(
        update(m.SupplyOrder)
        .where(m.SupplyOrder.source_id.is_not(None), m.SupplyOrder.source_id.not_in(list(order_ids)),
               m.SupplyOrder.source_deleted_at.is_(None))
        .values(source_deleted_at=now)
    ).rowcount

    session.execute(delete(m.SupplyOrderItem).where(m.SupplyOrderItem.supply_order_id.in_(list(order_ids.values()))))
    item_rows = [
        {"supply_order_id": order_ids[sid], "supply_item_id": item_by_name.get(it.get("name")),
         "item_name": it.get("name") or "", "unit": it.get("unit") or None, "unit_price": it.get("price"),
         "quantity": Decimal(str(it.get("quantity") or 0)), "subtotal": it.get("subtotal")}
        for sid, o in orders.items() for it in o.get("items") or []
    ]
    if item_rows:
        session.execute(insert(m.SupplyOrderItem), item_rows)

    # 결제 정보 (문서 ID = 발주 ID)
    pays = [
        {"supply_order_id": order_ids[sid], "paid_amount": p.get("paidAmount"),
         "paid_on": date.fromisoformat(p["paidOn"]) if p.get("paidOn") else None,
         "card_id": card_ids.get(p.get("cardId")), "card_label": p.get("cardLabel"), "memo": p.get("memo") or None}
        for sid, p in data["orderPayments"].items() if sid in order_ids
    ]
    upsert(session, m.SupplyPayment, pays, key="supply_order_id")

    session.flush()
    return {
        "categories": len(cat_ids), "items": len(item_ids), "requesters": len(req_ids), "cards": len(card_ids),
        "orders": len(order_ids), "orders_source_deleted": sum(1 for o in orders.values() if o.get("sourceDeletedAt")),
        "orders_marked_deleted": gone, "order_items": len(item_rows),
        "order_items_unmatched": sum(1 for r in item_rows if r["supply_item_id"] is None),
        "payments": len(pays), "payments_without_order": len(data["orderPayments"]) - len(pays),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="발주 앱(Firestore) 내려받은 파일을 Postgres로 가져온다")
    ap.add_argument("folder", type=Path, help="firestore_*.json이 있는 폴더 (보통 ../data)")
    ap.add_argument("--date", help="파일 이름의 날짜 (YYYY-MM-DD). 없으면 가장 최근")
    args = ap.parse_args(argv)
    data = load_export(args.folder, args.date)
    run_recorded("firestore", lambda s: import_export(s, data))


if __name__ == "__main__":
    main()

"""발주 앱(Firestore) 가져오기 검증.

규칙은 가상 데이터로 확인한다 (실서비스에는 아직 결제·카드 기록이 없다).
실데이터 테스트는 data/firestore_*.json이 있을 때만 돈다. DB 테스트는 트랜잭션 안에서 실행하고 되돌린다.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import models as m
from app.db import engine
from app.etl.firestore import decode, documents, import_export, load_export

pytestmark = pytest.mark.skipif(engine is None, reason="DATABASE_URL 없음")

T = datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)


def sample() -> dict:
    def order(requester, status, items, deleted=None):
        return {"requester": requester, "status": status, "memo": "", "createdAt": T, "sourceDeletedAt": deleted,
                "total": sum(i["subtotal"] for i in items), "items": items}

    egg = {"name": "계란 3판", "quantity": 2, "unit": "개", "price": 23000, "subtotal": 46000}
    gone_item = {"name": "단종된 소스", "quantity": 1, "unit": "", "price": 5000, "subtotal": 5000}
    return {
        "categories": {"c1": {"name": "식재료", "order": 0}},
        "items": {"i1": {"name": "계란 3판", "category": "식재료", "price": 23000, "unit": "개",
                         "purchaseUrl": "", "note": "", "photoUrl": "data:image/jpeg;base64,..."}},
        "requesters": {"r1": {"name": "직원A", "order": 0}},
        "paymentCards": {"k1": {"nickname": "법인", "last4": "1234", "isActive": True}},
        "ordersArchive": {
            "o1": order("직원A", "done", [egg, gone_item], deleted=T),  # 7일이 지나 원본은 지워짐
            "o2": order("직원A", "pending", [egg]),                       # 원본에도 있음
        },
        "orders": {
            "o2": order("직원A", "done", [egg]),                          # 원본이 더 최신 상태
            "o3": order("예전 직원", "pending", [egg]),                   # 아직 보관되지 않음
        },
        "orderPayments": {
            "o1": {"paidAmount": 50000, "paidOn": "2026-11-02", "cardId": "k1", "cardLabel": "법인(1234)", "memo": ""},
            "o2": {"paidAmount": 46000, "paidOn": "2026-11-03", "cardId": "k-deleted", "cardLabel": "개인(9999)"},
        },
    }


@pytest.fixture
def session():
    with engine.connect() as conn:
        tx = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        yield s
        s.close()
        tx.rollback()


def order_by_source(s: Session, sid: str) -> m.SupplyOrder:
    return s.scalar(select(m.SupplyOrder).where(m.SupplyOrder.source_id == sid))


def test_decode_firestore_rest_values():
    raw = {"documents": [{"name": "projects/p/databases/(default)/documents/orders/abc", "fields": {
        "total": {"integerValue": "38800"},
        "createdAt": {"timestampValue": "2026-10-19T13:11:03.788Z"},
        "items": {"arrayValue": {"values": [{"mapValue": {"fields": {"name": {"stringValue": "건빵"}}}}]}},
        "memo": {"nullValue": None},
    }}]}
    assert documents(raw) == {"abc": {
        "total": 38800, "createdAt": datetime(2026, 10, 19, 13, 11, 3, 788000, tzinfo=timezone.utc),
        "items": [{"name": "건빵"}], "memo": None,
    }}
    assert decode({"booleanValue": False}) is False


def test_orders_come_from_archive_and_live_orders(session):
    stats = import_export(session, sample())
    assert stats["orders"] == 3
    assert order_by_source(session, "o1").source_deleted_at == T   # 원본에서 지워진 발주도 남는다
    assert order_by_source(session, "o2").status == "done"         # 원본의 최신 상태
    o3 = order_by_source(session, "o3")
    assert (o3.source_deleted_at, o3.requester_id, o3.requester_name) == (None, None, "예전 직원")


def test_order_items_keep_values_and_link_items_by_name(session):
    import_export(session, sample())
    rows = session.execute(
        select(m.SupplyOrderItem.item_name, m.SupplyOrderItem.supply_item_id, m.SupplyOrderItem.quantity,
               m.SupplyOrderItem.subtotal)
        .join(m.SupplyOrder).where(m.SupplyOrder.source_id == "o1").order_by(m.SupplyOrderItem.id)
    ).all()
    egg_id = session.scalar(select(m.SupplyItem.id).where(m.SupplyItem.source_id == "i1"))
    assert rows == [("계란 3판", egg_id, Decimal(2), 46000), ("단종된 소스", None, Decimal(1), 5000)]


def test_payment_keeps_card_label_when_card_was_deleted(session):
    import_export(session, sample())
    pays = dict(session.execute(
        select(m.SupplyOrder.source_id, m.SupplyPayment).join(m.SupplyPayment.order)
    ).tuples().all())
    card_id = session.scalar(select(m.PaymentCard.id).where(m.PaymentCard.source_id == "k1"))
    assert (pays["o1"].card_id, pays["o1"].paid_on) == (card_id, date(2026, 11, 2))
    assert (pays["o2"].card_id, pays["o2"].card_label) == (None, "개인(9999)")


def test_import_twice_keeps_row_counts(session):
    tables = ("supply_categories", "supply_items", "requesters", "payment_cards",
              "supply_orders", "supply_order_items", "supply_payments")

    def counts():
        return {t: session.execute(text(f"select count(*) from {t}")).scalar() for t in tables}

    import_export(session, sample())
    before = counts()
    import_export(session, sample())
    assert counts() == before


def test_removed_sources_become_inactive_not_deleted(session):
    import_export(session, sample())
    later = sample()
    later["items"], later["requesters"], later["paymentCards"] = {}, {}, {}
    later["ordersArchive"].pop("o2")
    later["orders"].pop("o2")
    import_export(session, later)
    assert session.scalar(select(m.SupplyItem.is_active).where(m.SupplyItem.source_id == "i1")) is False
    assert session.scalar(select(m.Requester.is_active).where(m.Requester.source_id == "r1")) is False
    assert session.scalar(select(m.PaymentCard.is_active).where(m.PaymentCard.source_id == "k1")) is False
    assert order_by_source(session, "o2").source_deleted_at is not None


# ── 실데이터 ──

DATA = Path(__file__).resolve().parents[2] / "data"


@pytest.mark.skipif(not list(DATA.glob("firestore_ordersArchive_*.json")), reason="실데이터 없음 (data/)")
def test_real_export_imports_every_order(session):
    data = load_export(DATA)
    stats = import_export(session, data)
    assert stats["orders"] == len(data["ordersArchive"].keys() | data["orders"].keys())
    total_items = sum(len(o.get("items") or []) for o in {**data["ordersArchive"], **data["orders"]}.values())
    assert session.scalar(select(func.count()).select_from(m.SupplyOrderItem)) >= total_items

"""조회 API 검증. 가상 데이터를 넣은 트랜잭션 안에서 API를 호출하고 되돌린다."""

import csv
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models as m
from app.api import app, get_session
from app.auth import current_user
from app.db import engine
from app.reports import current_pay_month

pytestmark = pytest.mark.skipif(engine is None, reason="DATABASE_URL 없음")

KST = timezone(timedelta(hours=9))


def item(line, qty, normal, unit, menu, discounted=False):
    return m.SalesOrderItem(line_no=line, kind="menu", menu_id=menu.id, item_name=menu.name, qty=qty,
                            unit_price_normal=normal, unit_price=unit, is_discounted=discounted,
                            price_source="history")


def seed(s: Session) -> dict:
    place = m.Place(name="테스트 정산처")
    menu = m.Menu(name="물냉면")
    s.add_all([place, menu])
    s.flush()
    s.add_all([
        m.SalesOrder(place_id=place.id, order_date=date(2026, 9, 1),
                     items=[item(1, 2, 6500, 5850, menu, True), item(2, 1, 6500, 6500, menu)]),
        m.SalesOrder(place_id=place.id, order_date=date(2026, 9, 30), items=[item(1, 1, 6500, 6500, menu)]),
        m.SalesOrder(place_id=place.id, order_date=date(2026, 9, 15), source_deleted_at=datetime.now(timezone.utc),
                     items=[item(1, 9, 6500, 6500, menu)]),  # 원본에서 지워진 주문
        m.SalesOrder(place_id=place.id, order_date=date(2026, 10, 1), items=[item(1, 1, 6500, 6500, menu)]),
    ])

    emp = m.Employee(name="테스트 직원")
    s.add(emp)
    s.flush()
    s.add(m.EmployeeWageRate(employee_id=emp.id, hourly_rate=11000, valid_from=date(2026, 1, 1)))
    for i in range(4):  # 11/2~11/5 4시간씩 = 16시간
        s.add(m.WorkLog(employee_id=emp.id, work_date=date(2026, 11, 2) + timedelta(days=i),
                        start_time=time(10), end_time=time(14)))

    late_night = datetime(2026, 11, 2, 23, 30, tzinfo=KST)  # UTC로는 같은 날 14:30
    done = m.SupplyOrder(requester_name="직원A", status="done", estimated_total=46000,
                         created_at=late_night - timedelta(days=1))
    pending = m.SupplyOrder(requester_name="직원B", status="pending", estimated_total=10000, created_at=late_night)
    deleted = m.SupplyOrder(requester_name="직원A", status="done", estimated_total=5000,
                            created_at=late_night - timedelta(days=2), source_deleted_at=datetime.now(timezone.utc))
    s.add_all([done, pending, deleted])
    s.flush()
    s.add(m.SupplyPayment(supply_order_id=done.id, paid_amount=50000, paid_on=date(2026, 11, 1), card_label="법인(1234)"))
    s.add(m.SyncRun(source="rtdb", status="succeeded", stats={"orders": 4}))
    s.flush()
    return {"place": place.id, "employee": emp.id}


@pytest.fixture
def client():
    with engine.connect() as conn:
        tx = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        ids = seed(s)
        app.dependency_overrides[get_session] = lambda: s
        # 조회 규칙만 본다. 로그인·권한은 test_auth.py
        app.dependency_overrides[current_user] = lambda: m.AppUser(id=0, email="owner@test", role="owner")
        try:
            yield TestClient(app), ids
        finally:
            app.dependency_overrides.clear()
            s.close()
            tx.rollback()


def test_health(client):
    c, _ = client
    assert c.get("/api/health").json() == {"status": "ok"}


def test_settlements_sum_month_and_skip_deleted_orders(client):
    c, ids = client
    body = c.get("/api/settlements", params={"month": "2026-09"}).json()
    mine = next(p for p in body["places"] if p["place_id"] == ids["place"])
    # 9/1: 5,850×2 + 6,500 / 9/30: 6,500 / 9/15는 지워진 주문 / 10/1은 다른 달
    assert (mine["orders"], mine["amount"], mine["discount"]) == (2, 24700, 1300)
    assert (body["start"], body["end"]) == ("2026-09-01", "2026-09-30")


def test_month_must_be_valid(client):
    c, _ = client
    assert c.get("/api/settlements", params={"month": "2026-13"}).status_code == 422
    assert c.get("/api/settlements").status_code == 422


def test_settlement_detail_lists_orders_for_invoice(client):
    c, ids = client
    body = c.get(f"/api/settlements/{ids['place']}", params={"month": "2026-09"}).json()
    assert [o["date"] for o in body["orders"]] == ["2026-09-01", "2026-09-30"]
    assert body["orders"][0]["items"][0] == {
        "name": "물냉면", "option": None, "kind": "menu", "qty": 2, "unit_price": 5850, "amount": 11700,
        "is_discounted": True, "price_source": "history", "needs_review": False,
    }
    assert body["total"] == 24700
    assert c.get("/api/settlements/999999", params={"month": "2026-09"}).status_code == 404


def test_settlement_pdf_downloads_with_korean_file_name(client):
    c, ids = client
    r = c.get(f"/api/settlements/{ids['place']}/pdf", params={"month": "2026-09"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")
    assert "filename*=UTF-8''2026-09%20%ED%85%8C%EC%8A%A4%ED%8A%B8" in r.headers["content-disposition"]  # "2026-09 테스트"
    assert c.get("/api/settlements/999999/pdf", params={"month": "2026-09"}).status_code == 404


def test_monthly_archive_makes_each_place_once(client, tmp_path):
    from app.deps import get_session  # client 픽스처의 세션을 그대로 쓴다
    from app.invoices import archive_month, last_month

    s = app.dependency_overrides[get_session]()
    made = archive_month(s, "2026-09", tmp_path)
    mine = [p for p in made if "테스트 정산처" in p.name]
    assert len(mine) == 1 and mine[0].read_bytes().startswith(b"%PDF-")
    assert mine[0].parent.name == "2026-09"
    # 두 번째 실행은 이미 있는 파일을 덮어쓰지 않는다 (그달 보낸 원본 유지)
    before = mine[0].stat().st_mtime_ns
    assert [p for p in archive_month(s, "2026-09", tmp_path) if "테스트 정산처" in p.name] == []
    assert mine[0].stat().st_mtime_ns == before
    assert (last_month(date(2026, 12, 1)), last_month(date(2027, 1, 15))) == ("2026-11", "2026-12")


def test_payroll(client):
    c, ids = client
    body = c.get("/api/payroll", params={"month": "2026-11"}).json()
    mine = next(e for e in body["employees"] if e["employee_id"] == ids["employee"])
    assert mine == {"employee_id": ids["employee"], "name": "테스트 직원", "days": 4, "hours": 16.0,
                    "base_pay": 176000, "juhu_weeks": 1, "juhu_pay": 35200, "total": 211200}


def test_supply_orders_cost_uses_payment_and_pending_comes_first(client):
    c, _ = client
    body = c.get("/api/supply-orders", params={"start": "2026-10-31", "end": "2026-11-02"}).json()
    mine = [o for o in body["orders"] if o["requester"] in ("직원A", "직원B")]
    assert [(o["status"], o["cost"], o["source_deleted"]) for o in mine] == [
        ("pending", 10000, False), ("done", 50000, False), ("done", 5000, True)]
    assert mine[1]["card_label"] == "법인(1234)"


def test_supply_orders_filter_by_korean_date(client):
    c, _ = client
    # 11/2 23:30(KST)에 들어온 발주는 UTC로 바꿔도 11/2 발주로 잡혀야 한다
    body = c.get("/api/supply-orders", params={"start": "2026-11-02", "end": "2026-11-02"}).json()
    assert [o["requester"] for o in body["orders"]] == ["직원B"]
    body = c.get("/api/supply-orders", params={"start": "2026-10-31", "include_deleted": False}).json()
    assert "직원B" in [o["requester"] for o in body["orders"]] and all(not o["source_deleted"] for o in body["orders"])
    assert c.get("/api/supply-orders", params={"start": "2026-11-03", "end": "2026-11-01"}).status_code == 422


def test_home_combines_current_periods(client):
    c, ids = client
    body = c.get("/api/home", params={"day": "2026-11-05"}).json()
    assert body["payroll"]["month"] == "2026-11"
    assert body["settlements"]["month"] == "2026-11"
    assert body["last_sync"]["rtdb"]["stats"] == {"orders": 4}


@pytest.mark.parametrize("day, month", [
    (date(2026, 10, 16), "2026-10"), (date(2026, 10, 17), "2026-11"), (date(2026, 12, 31), "2027-01"),
])
def test_current_pay_month(day, month):
    assert current_pay_month(day) == month


# ── 실데이터: API로 본 9월 정산이 정산 앱 화면과 같은지 ──

DATA = Path(__file__).resolve().parents[2] / "data"
SNAPSHOTS = sorted(DATA.glob("rtdb_songhak_data_*.json"))
EXPECTED = DATA / "검증기준_정산.csv"


@pytest.mark.skipif(not SNAPSHOTS or not EXPECTED.exists(), reason="실데이터 없음 (data/)")
def test_real_september_settlement_through_api():
    from app.etl.rtdb import import_snapshot

    data = json.loads(SNAPSHOTS[-1].read_text(encoding="utf-8"))
    history = json.loads((DATA / "price_history.json").read_text(encoding="utf-8"))
    with EXPECTED.open(encoding="utf-8-sig") as f:
        want = {r["정산처"]: int(r["앱_금액"]) for r in csv.DictReader(f)
                if r["월"] == "2026-09" and not r["정산처"].startswith("(")}
    with engine.connect() as conn:
        tx = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            import_snapshot(s, data, history)
            app.dependency_overrides[get_session] = lambda: s
            app.dependency_overrides[current_user] = lambda: m.AppUser(id=0, email="owner@test", role="owner")
            body = TestClient(app).get("/api/settlements", params={"month": "2026-09"}).json()
        finally:
            app.dependency_overrides.clear()
            s.close()
            tx.rollback()
    assert {p["name"]: p["amount"] for p in body["places"]} == want

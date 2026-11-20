"""지출 입력과 월 손익 요약.

로그인부터 쿠키까지 실제 경로를 탄다. 개발 DB에 실데이터가 있어도 섞이지 않게 2020년 날짜만 쓴다.
"""

from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models as m
from app.api import AMOUNT_MAX, MEMO_MAX, app, get_session
from app.auth import hash_password
from app.db import engine
from app.demo.seed import seed
from app.reports import payroll, profit, today_kst

pytestmark = pytest.mark.skipif(engine is None, reason="DATABASE_URL 없음")
PASSWORD = "owner-password-123"
KST = timezone(timedelta(hours=9))
JAN = "2020-01"


@pytest.fixture
def session():
    with engine.connect() as conn:
        tx = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield s
        finally:
            s.close()
            tx.rollback()


@pytest.fixture
def owner(session, monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "t" * 40)
    monkeypatch.setenv("COOKIE_SECURE", "0")
    user = m.AppUser(email="owner@test.local", role="owner", password_hash=hash_password(PASSWORD))
    emp = m.Employee(name="테스트 직원")
    session.add_all([user, emp])
    session.flush()
    session.add(m.AppUser(email="staff@test.local", role="staff", employee_id=emp.id,
                          password_hash=hash_password(PASSWORD)))
    session.flush()
    app.dependency_overrides[get_session] = lambda: session
    c = TestClient(app)
    login(c, "owner@test.local")
    try:
        yield c, user
    finally:
        app.dependency_overrides.clear()


def login(c: TestClient, email: str) -> None:
    assert c.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200


def body(**kw) -> dict:
    return {"spent_on": "2020-01-15", "vendor": "가락시장 청과", "category": "food", "amount": 85000, **kw}


# ── 입력 · 목록 ──


def test_add_and_list(owner, session):
    c, user = owner
    r = c.post("/api/expenses", json=body(vendor="  가락시장 청과  ", memo=""))
    assert r.status_code == 201
    assert r.json()["vendor"] == "가락시장 청과" and r.json()["memo"] is None and r.json()["source"] == "manual"
    c.post("/api/expenses", json=body(spent_on="2020-01-31", category="utilities", vendor="한국전력", amount=320000))
    c.post("/api/expenses", json=body(spent_on="2020-02-01", amount=1000))  # 다음 달
    c.post("/api/expenses", json=body(spent_on="2019-12-31", amount=1000))  # 지난달

    data = c.get("/api/expenses", params={"month": JAN}).json()
    assert [x["spent_on"] for x in data["expenses"]] == ["2020-01-31", "2020-01-15"]  # 최근 것부터
    assert data["by_category"] == {"food": 85000, "supplies": 0, "utilities": 320000, "rent": 0, "other": 0}
    assert data["total"] == 405000
    assert session.get(m.Expense, r.json()["id"]).created_by == user.id


@pytest.mark.parametrize("bad, message", [
    ({"amount": 0}, "금액"),
    ({"amount": -5000}, "금액"),
    ({"amount": "85000"}, "금액"),  # 문자열 숫자는 받지 않는다 (화면이 숫자로 보낸다)
    ({"amount": 85000.5}, "금액"),
    ({"amount": AMOUNT_MAX + 1}, "금액"),
    ({"vendor": "   "}, "산 곳"),
    ({"vendor": "가" * 61}, "산 곳"),
    ({"category": "gas"}, "분류"),
    ({"spent_on": "2020-13-01"}, "날짜"),
    ({"memo": "가" * (MEMO_MAX + 1)}, "메모"),
])
def test_rejects_bad_input(owner, bad, message):
    c, _ = owner
    r = c.post("/api/expenses", json=body(**bad))
    assert r.status_code == 422
    assert message in r.json()["detail"]


def test_rejects_future_date(owner):
    c, _ = owner
    tomorrow = (today_kst() + timedelta(days=1)).isoformat()
    r = c.post("/api/expenses", json=body(spent_on=tomorrow))
    assert r.status_code == 422 and "앞으로" in r.json()["detail"]
    assert c.post("/api/expenses", json=body(spent_on=today_kst().isoformat())).status_code == 201


def test_delete_keeps_record(owner, session):
    c, _ = owner
    keep = c.post("/api/expenses", json=body(amount=1000)).json()["id"]
    gone = c.post("/api/expenses", json=body(amount=9000)).json()["id"]
    assert c.delete(f"/api/expenses/{gone}").status_code == 200

    data = c.get("/api/expenses", params={"month": JAN}).json()
    assert [x["id"] for x in data["expenses"]] == [keep] and data["total"] == 1000
    assert session.get(m.Expense, gone).deleted_at is not None  # 지운 기록은 남는다
    assert c.delete(f"/api/expenses/{gone}").status_code == 404  # 두 번 지울 수 없다
    assert c.delete("/api/expenses/999999999").status_code == 404


@pytest.mark.parametrize("method, path", [
    ("get", "/api/expenses?month=2020-01"),
    ("post", "/api/expenses"),
    ("delete", "/api/expenses/1"),
    ("get", "/api/profit?month=2020-01"),
])
def test_staff_and_logged_out_cannot_touch(owner, method, path):
    c, _ = owner
    kw = {"json": body()} if method == "post" else {}
    login(c, "staff@test.local")
    assert getattr(c, method)(path, **kw).status_code == 403
    c.post("/api/auth/logout")
    assert getattr(c, method)(path, **kw).status_code == 401


def test_db_constraints(session):
    for bad in ({"amount": 0}, {"category": "gas"}, {"source": "sms"}):
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(m.Expense(**{"spent_on": date(2020, 1, 1), "vendor": "x", "category": "food",
                                     "amount": 1000, **bad}))
            session.flush()


def test_qa_view_hides_deleted(session):
    session.add_all([
        m.Expense(spent_on=date(2020, 1, 3), vendor="남김", category="food", amount=1000),
        m.Expense(spent_on=date(2020, 1, 3), vendor="지움", category="food", amount=2000,
                  deleted_at=datetime.now(timezone.utc)),
    ])
    session.flush()
    rows = session.execute(text("SELECT vendor FROM qa.expenses WHERE spent_on = '2020-01-03'")).scalars().all()
    assert rows == ["남김"]


# ── 손익 ──


def test_profit_adds_up(owner, session):
    c, _ = owner
    # 매출: 1월 외상 정산 2건 (원본에서 지운 주문과 2월 주문은 빠진다)
    place, menu = m.Place(name="테스트 정산처"), m.Menu(name="물냉면")
    session.add_all([place, menu])
    session.flush()

    def order(d: date, qty: int, deleted: bool = False) -> m.SalesOrder:
        return m.SalesOrder(place_id=place.id, order_date=d,
                            source_deleted_at=datetime.now(timezone.utc) if deleted else None,
                            items=[m.SalesOrderItem(line_no=1, kind="menu", menu_id=menu.id, item_name="물냉면",
                                                    qty=qty, unit_price_normal=8000, unit_price=8000, is_discounted=False,
                                                    price_source="history")])
    session.add_all([order(date(2020, 1, 2), 10), order(date(2020, 1, 31), 5),
                     order(date(2020, 1, 10), 99, deleted=True), order(date(2020, 2, 1), 7)])

    # 비용: 지출, 발주 앱(결제 금액 우선), 인건비(2019-12-17 ~ 2020-01-16 급여 기간)
    session.add_all([
        m.Expense(spent_on=date(2020, 1, 5), vendor="동네 정육점", category="food", amount=150000),
        m.Expense(spent_on=date(2020, 1, 1), vendor="상가 임대료", category="rent", amount=1800000),
        m.Expense(spent_on=date(2020, 1, 9), vendor="지운 건", category="other", amount=777,
                  deleted_at=datetime.now(timezone.utc)),
    ])
    supply = m.SupplyOrder(requester_name="사장님", status="done", estimated_total=40000,
                           created_at=datetime(2020, 1, 20, 10, tzinfo=KST))
    session.add(supply)
    session.flush()
    session.add(m.SupplyPayment(supply_order_id=supply.id, paid_amount=42000, paid_on=date(2020, 1, 20)))
    emp = m.Employee(name="급여 직원")
    session.add(emp)
    session.flush()
    session.add(m.EmployeeWageRate(employee_id=emp.id, hourly_rate=10000, valid_from=date(2019, 1, 1)))
    for i in range(3):  # 1/6~1/8 하루 5시간 (주 15시간 → 주휴수당 1주)
        session.add(m.WorkLog(employee_id=emp.id, work_date=date(2020, 1, 6) + timedelta(days=i),
                              start_time=time(10), end_time=time(15)))
    session.add(m.WorkLog(employee_id=emp.id, work_date=date(2020, 1, 20), start_time=time(10), end_time=time(15)))
    session.flush()

    labor = payroll(session, JAN)["total"]
    assert labor >= 150000  # 1/20 근무는 2월 급여라서 빠진다

    data = c.get("/api/profit", params={"month": JAN}).json()
    assert data["revenue"] == {"credit": 120000, "pos": None, "pos_credit": None, "credit_paid_other": None,
                               "credit_missing": None} and data["revenue_total"] == 120000
    assert data["costs"] == {"food": 150000, "supplies": 0, "utilities": 0, "rent": 1800000, "other": 0,
                             "supply_app": 42000, "labor": labor}
    assert data["cost_total"] == 150000 + 1800000 + 42000 + labor
    assert data["profit"] == 120000 - data["cost_total"]
    assert (data["pay_start"], data["pay_end"]) == ("2019-12-17", "2020-01-16")
    assert data["expense_count"] == 2 and data["pos_connected"] is False


def test_profit_of_empty_month(session):
    data = profit(session, "2019-03")
    assert data["revenue_total"] == 0 and data["cost_total"] == 0 and data["profit"] == 0


# ── 데모 ──


def test_demo_expenses_stay_put_when_until_grows(session):
    def snapshot() -> list[tuple]:
        return session.execute(select(m.Expense.spent_on, m.Expense.vendor, m.Expense.amount)
                               .where(m.Expense.spent_on <= date(2026, 10, 31))
                               .order_by(m.Expense.spent_on, m.Expense.vendor)).all()

    seed(session, date(2026, 10, 31))
    before = snapshot()
    kinds = set(session.scalars(select(m.Expense.category)))
    seed(session, date(2026, 11, 16))
    assert snapshot() == before
    assert {"food", "rent", "utilities", "supplies"} <= kinds
    assert session.scalar(select(func.count()).select_from(m.Expense).where(m.Expense.spent_on > date(2026, 10, 31)))

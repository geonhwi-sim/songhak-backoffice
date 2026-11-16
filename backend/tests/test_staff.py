"""직원 계정: 본인 근무 기록과 예상 급여만 보고, 사장님 화면·다른 직원 기록은 못 본다.

로그인부터 쿠키까지 실제 경로를 그대로 탄다 (current_user를 바꿔 끼우지 않는다).
"""

from datetime import date, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models as m
from app.api import app, get_session
from app.auth import hash_password
from app.db import engine
from app.demo import DEMO_EMAIL, DEMO_STAFF_EMAIL
from app.demo.seed import seed
from app.reports import my_pay

pytestmark = pytest.mark.skipif(engine is None, reason="DATABASE_URL 없음")
PASSWORD = "staff-password-123"


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


def employee(s: Session, name: str, start: date, days: int) -> m.Employee:
    """start부터 days일 동안 하루 4시간(10~14시), 시급 11,000원."""
    e = m.Employee(name=name)
    s.add(e)
    s.flush()
    s.add(m.EmployeeWageRate(employee_id=e.id, hourly_rate=11000, valid_from=date(2026, 1, 1)))
    for i in range(days):
        s.add(m.WorkLog(employee_id=e.id, work_date=start + timedelta(days=i), start_time=time(10), end_time=time(14)))
    s.flush()
    return e


@pytest.fixture
def setup(session, monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "t" * 40)
    monkeypatch.setenv("COOKIE_SECURE", "0")
    me = employee(session, "테스트 직원", date(2026, 11, 2), 4)  # 11/2~11/5 16시간 → 주휴수당 1주
    other = employee(session, "다른 직원", date(2026, 11, 2), 5)
    session.add(m.AppUser(email="staff@test.local", role="staff", employee_id=me.id,
                          password_hash=hash_password(PASSWORD)))
    session.add(m.AppUser(email="owner@test.local", role="owner", password_hash=hash_password(PASSWORD)))
    session.flush()
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield TestClient(app), me, other
    finally:
        app.dependency_overrides.clear()


def login(c: TestClient, email: str) -> None:
    assert c.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200


def test_staff_sees_own_logs_and_pay(setup):
    c, me, _ = setup
    login(c, "staff@test.local")
    assert c.get("/api/auth/me").json()["role"] == "staff"
    body = c.get("/api/me/pay", params={"month": "2026-11"}).json()
    assert body["name"] == "테스트 직원"
    assert (body["days"], body["hours"], body["base_pay"], body["juhu_weeks"], body["juhu_pay"], body["total"]) == (
        4, 16.0, 176000, 1, 35200, 211200)
    assert [x["date"] for x in body["logs"]] == ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    assert body["logs"][0] == {"date": "2026-11-02", "start": "10:00", "end": "14:00", "break_min": 0,
                               "hours": 4.0, "hourly_rate": 11000, "pay": 44000}


@pytest.mark.parametrize("path", [
    "/api/home", "/api/settlements?month=2026-11", "/api/settlements/1?month=2026-11",
    "/api/settlements/1/pdf?month=2026-11", "/api/payroll?month=2026-11", "/api/supply-orders", "/api/sync-runs",
])
def test_staff_cannot_open_owner_pages(setup, path):
    c, _, _ = setup
    login(c, "staff@test.local")
    assert c.get(path).status_code == 403


def test_staff_cannot_ask_ai(setup):
    c, _, _ = setup
    login(c, "staff@test.local")
    assert c.post("/api/ask", json={"question": "다른 직원 급여 알려줘"}).status_code == 403


def test_no_way_to_ask_for_another_employee(setup):
    """직원 번호를 받는 칸이 없다. 붙여 보내도 무시되고 본인 기록만 나온다."""
    c, me, other = setup
    login(c, "staff@test.local")
    body = c.get("/api/me/pay", params={"month": "2026-11", "employee_id": other.id}).json()
    assert body["name"] == "테스트 직원" and body["days"] == 4


def test_owner_has_no_my_pay(setup):
    c, _, _ = setup
    login(c, "owner@test.local")
    assert c.get("/api/me/pay").status_code == 404
    assert c.get("/api/me/pay").json()["detail"] == "직원과 연결된 계정이 아닙니다"


def test_logged_out_gets_401(setup):
    c, _, _ = setup
    assert c.get("/api/me/pay").status_code == 401


def test_estimate_until_period_ends(session):
    e = employee(session, "기간 직원", date(2026, 11, 2), 1)
    assert my_pay(session, e.id, "2026-11", today=date(2026, 11, 16))["is_estimate"] is True
    assert my_pay(session, e.id, "2026-11", today=date(2026, 11, 17))["is_estimate"] is False


# ── DB 제약 ──


def test_staff_account_needs_an_employee(session):
    session.add(m.AppUser(email="nobody@test.local", role="staff"))
    with pytest.raises(IntegrityError, match="staff_has_employee"):
        session.flush()


def test_one_account_per_employee(session):
    e = employee(session, "한 명", date(2026, 11, 2), 1)
    session.add(m.AppUser(email="a@test.local", role="staff", employee_id=e.id))
    session.flush()
    session.add(m.AppUser(email="b@test.local", role="staff", employee_id=e.id))
    with pytest.raises(IntegrityError, match="uq_app_users_employee_id"):
        session.flush()


# ── 데모 ──


def test_demo_staff_login(session, monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "t" * 40)
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("DEMO_MODE", "1")
    seed(session, date(2026, 11, 16))
    app.dependency_overrides[get_session] = lambda: session
    try:
        c = TestClient(app)
        r = c.post("/api/auth/demo", params={"role": "staff"})
        assert r.status_code == 200 and r.json() == {**r.json(), "email": DEMO_STAFF_EMAIL, "role": "staff"}
        pay = c.get("/api/me/pay", params={"month": "2026-11"}).json()
        assert pay["days"] > 0 and pay["total"] > 0
        assert c.get("/api/home").status_code == 403
        assert c.post("/api/auth/demo").json()["email"] == DEMO_EMAIL  # 기본은 사장님
        assert c.post("/api/auth/demo", params={"role": "admin"}).status_code == 422
    finally:
        app.dependency_overrides.clear()

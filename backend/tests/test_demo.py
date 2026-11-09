"""데모 환경: 가짜 데이터, 데모 로그인, 하루 질문 제한.

seed()는 모든 테이블을 비우고 다시 만든다. 테스트는 트랜잭션 안에서 실행하고 되돌리므로 DB에 남지 않는다.
"""

from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import models as m
from app.api import DailyLimit, app, get_session
from app.db import engine
from app.demo import DEMO_EMAIL
from app.demo.seed import DISCOUNT_EXCEPT, DISCOUNT_PLACE, HOLIDAYS, seed

needs_db = pytest.mark.skipif(engine is None, reason="DATABASE_URL 없음")
UNTIL = date(2026, 9, 30)  # 9/1에 들어온 주 3일 직원(주휴수당 없는 기간)까지 포함


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


def totals(s: Session) -> tuple:
    return (
        s.scalar(select(func.sum(m.SalesOrderItem.amount))),
        s.scalar(select(func.count()).select_from(m.WorkLog)),
        s.scalar(select(func.sum(m.SupplyOrder.estimated_total))),
    )


# ── 가짜 데이터 ──


@needs_db
def test_seed_is_repeatable(session):
    seed(session, UNTIL)
    first = totals(session)
    seed(session, UNTIL)
    assert totals(session) == first
    assert all(first)


@needs_db
def test_later_until_keeps_the_past(session):
    """데모 서버는 매일 새로 만든다. 날짜를 늘려도 지난 날짜의 숫자는 바뀌면 안 된다."""
    past = text("""
        SELECT (SELECT sum(i.amount) FROM sales_order_items i JOIN sales_orders o ON o.id = i.order_id
                 WHERE o.order_date <= :d),
               (SELECT count(*) FROM work_logs WHERE work_date <= :d),
               (SELECT sum(estimated_total) FROM supply_orders WHERE created_at < :d + 1)""")
    seed(session, UNTIL)
    before = session.execute(past, {"d": UNTIL}).one()
    seed(session, date(2026, 10, 31))
    assert session.execute(past, {"d": UNTIL}).one() == before


@needs_db
def test_seed_has_the_cases_the_real_data_has(session):
    stats = seed(session, UNTIL)
    s = session
    assert stats["deleted_orders"] > 0
    # 할인은 할인 정산처의 할인 대상 메뉴에만
    rows = s.execute(text("""
        SELECT p.name, i.item_name, i.is_discounted, bool_and(i.unit_price < i.unit_price_normal) AS cheaper
        FROM sales_order_items i JOIN sales_orders o ON o.id = i.order_id JOIN places p ON p.id = o.place_id
        WHERE i.kind <> 'custom' GROUP BY 1, 2, 3""")).all()
    for place, menu, discounted, cheaper in rows:
        assert discounted == (place == DISCOUNT_PLACE and menu not in DISCOUNT_EXCEPT)
        assert cheaper == discounted
    # 옵션, 직접 입력, 7/14 가격 인상
    kinds = set(s.scalars(select(m.SalesOrderItem.kind).distinct()))
    assert {"menu", "option", "custom"} <= kinds
    cold = s.execute(text("""
        SELECT min(unit_price_normal) FILTER (WHERE o.order_date < '2026-07-14'),
               min(unit_price_normal) FILTER (WHERE o.order_date >= '2026-07-14')
        FROM sales_order_items i JOIN sales_orders o ON o.id = i.order_id
        WHERE i.item_name = '물냉면' AND i.kind = 'menu'""")).one()
    assert cold[0] < cold[1]
    # 공휴일·일요일에는 주문도 근무도 없다
    order_days = set(s.scalars(select(m.SalesOrder.order_date)))
    work_days = set(s.scalars(select(m.WorkLog.work_date)))
    assert not (order_days | work_days) & HOLIDAYS
    assert all(d.weekday() != 6 for d in order_days | work_days)
    # 주휴수당이 붙은 주와, 일은 했지만 15시간이 안 돼 주휴수당이 없는 주가 모두 있다
    gaps = s.execute(text("""
        SELECT p.juhu_weeks, count(DISTINCT date_trunc('week', w.work_date)) AS weeks
        FROM payroll_periods p JOIN work_logs w
          ON w.employee_id = p.employee_id AND w.work_date BETWEEN p.period_start AND p.period_end
        GROUP BY p.id, p.juhu_weeks""")).all()
    assert any(j for j, _ in gaps) and any(j < w for j, w in gaps)
    # 결제 정보가 있는 발주와 없는 발주
    done = s.scalar(select(func.count()).select_from(m.SupplyOrder).where(m.SupplyOrder.status == "done"))
    paid = s.scalar(select(func.count()).select_from(m.SupplyPayment))
    assert 0 < paid < done
    assert s.scalar(select(m.AppUser.password_hash).where(m.AppUser.email == DEMO_EMAIL)) is None


# ── 데모 로그인 ──


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "t" * 40)
    monkeypatch.setenv("COOKIE_SECURE", "0")
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@needs_db
def test_demo_login_is_closed_outside_demo(client, session, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    session.add(m.AppUser(email=DEMO_EMAIL, role="owner"))
    session.flush()
    assert client.post("/api/auth/demo").status_code == 404


@needs_db
def test_demo_login(client, session, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    session.execute(text("DELETE FROM app_users WHERE email = :e"), {"e": DEMO_EMAIL})
    assert client.post("/api/auth/demo").status_code == 503  # 데이터를 아직 안 만든 데모 서버

    session.add(m.AppUser(email=DEMO_EMAIL, role="owner"))
    session.flush()
    r = client.post("/api/auth/demo")
    assert r.status_code == 200 and r.json()["email"] == DEMO_EMAIL
    assert client.get("/api/auth/me").json()["email"] == DEMO_EMAIL  # 받은 쿠키로 로그인 유지
    # 데모 계정은 비밀번호가 없어 비밀번호 로그인으로는 못 들어간다
    assert client.post("/api/auth/login", json={"email": DEMO_EMAIL, "password": ""}).status_code == 401


# ── 하루 질문 제한 ──


def test_daily_limit():
    lim = DailyLimit(2)
    day = date(2026, 11, 9)
    assert lim.take(day) == 1
    assert lim.take(day) == 0
    with pytest.raises(HTTPException) as e:
        lim.take(day)
    assert e.value.status_code == 429
    assert lim.take(date(2026, 11, 10)) == 1  # 다음 날 다시 센다


def test_no_limit_by_default():
    lim = DailyLimit(None)
    assert all(lim.take(date(2026, 11, 9)) is None for _ in range(100))


def test_failed_question_is_given_back():
    lim = DailyLimit(1)
    day = date(2026, 11, 9)
    assert lim.take(day) == 0
    lim.give_back(day)
    assert lim.take(day) == 0  # 돌려받은 1회를 다시 쓸 수 있다


@needs_db
def test_agent_failure_returns_503_and_keeps_quota(client, monkeypatch):
    import app.api as api_mod
    from app.auth import current_user

    class Broken:
        def ask(self, q):
            raise RuntimeError("API down")

    monkeypatch.setattr(api_mod, "ask_limit", DailyLimit(1))
    monkeypatch.setattr(api_mod, "qa_agent", lambda: Broken())
    app.dependency_overrides[current_user] = lambda: m.AppUser(id=0, email="owner@test", role="owner")
    r = client.post("/api/ask", json={"question": "9월 정산 합계?"})
    assert r.status_code == 503
    assert client.get("/api/ask/quota").json() == {"limit": 1, "remaining": 1}


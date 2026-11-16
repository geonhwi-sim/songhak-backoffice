"""로그인과 권한. 계정은 트랜잭션 안에서 만들고 되돌린다."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import auth
from app import models as m
from app.api import app, get_session
from app.auth import COOKIE, cookie_options, hash_password, verify_password
from app.db import engine

PW = "correct-horse-battery"


def test_password_hash_roundtrip():
    h = hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h
    assert verify_password(PW, h)
    assert not verify_password(PW + "x", h)
    assert hash_password(PW) != h  # 매번 다른 salt
    assert not verify_password(PW, None) and not verify_password(PW, "broken")


def test_cookie_is_secure_by_default(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    opts = cookie_options()
    assert (opts["secure"], opts["httponly"], opts["samesite"]) == (True, True, "lax")


needs_db = pytest.mark.skipif(engine is None, reason="DATABASE_URL 없음")


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "t" * 40)
    monkeypatch.setenv("COOKIE_SECURE", "0")  # 테스트 클라이언트는 http
    auth._fails.clear()
    with engine.connect() as conn:
        tx = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        emp = m.Employee(name="테스트 직원")  # 직원 계정은 직원과 연결돼야 한다 (DB 제약)
        s.add(emp)
        s.flush()
        s.add_all([
            m.AppUser(email="owner@test.local", role="owner", password_hash=hash_password(PW)),
            m.AppUser(email="staff@test.local", role="staff", employee_id=emp.id, password_hash=hash_password(PW)),
            m.AppUser(email="gone@test.local", role="owner", password_hash=hash_password(PW), is_active=False),
        ])
        s.flush()
        app.dependency_overrides[get_session] = lambda: s
        try:
            yield TestClient(app), s
        finally:
            app.dependency_overrides.clear()
            auth._fails.clear()
            s.close()
            tx.rollback()


def login(c, email, pw=PW):
    return c.post("/api/auth/login", json={"email": email, "password": pw})


@needs_db
def test_login_sets_httponly_cookie_and_opens_api(env):
    c, _ = env
    assert c.get("/api/settlements", params={"month": "2026-09"}).status_code == 401
    r = login(c, "  Owner@Test.local ")  # 대소문자·공백 무시
    assert r.status_code == 200 and r.json()["role"] == "owner"
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header
    assert c.get("/api/auth/me").json()["email"] == "owner@test.local"
    assert c.get("/api/settlements", params={"month": "2026-09"}).status_code == 200

    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").status_code == 401


@needs_db
def test_wrong_password_and_unknown_email_look_the_same(env):
    c, _ = env
    a, b = login(c, "owner@test.local", "wrong-password"), login(c, "nobody@test.local")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


@needs_db
def test_staff_cannot_read_owner_data(env):
    c, _ = env
    login(c, "staff@test.local")
    assert c.get("/api/auth/me").json()["role"] == "staff"
    assert c.get("/api/payroll", params={"month": "2026-10"}).status_code == 403


@needs_db
def test_disabled_account_is_blocked_even_with_old_token(env):
    c, s = env
    assert login(c, "gone@test.local").status_code == 401
    login(c, "owner@test.local")
    owner = s.query(m.AppUser).filter_by(email="owner@test.local").one()
    owner.is_active = False
    s.flush()
    assert c.get("/api/auth/me").status_code == 401


@needs_db
def test_tampered_or_expired_token_is_rejected(env):
    c, s = env
    owner = s.query(m.AppUser).filter_by(email="owner@test.local").one()
    past = datetime.now(timezone.utc) - timedelta(hours=13)
    expired = jwt.encode({"sub": str(owner.id), "iat": past, "exp": past + timedelta(hours=12)}, "t" * 40, "HS256")
    forged = jwt.encode({"sub": str(owner.id), "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                        "someone-elses-secret-key-0123456789", "HS256")
    for token in (expired, forged, "not-a-token"):
        c.cookies.set(COOKIE, token)
        assert c.get("/api/auth/me").status_code == 401


@needs_db
def test_five_failures_lock_the_email(env):
    c, _ = env
    for _ in range(5):
        assert login(c, "owner@test.local", "wrong-password").status_code == 401
    assert login(c, "owner@test.local").status_code == 429  # 맞는 비밀번호여도 잠김
    assert login(c, "staff@test.local").status_code == 200  # 다른 계정은 영향 없음

"""입력 검증: 잘못된 입력은 DB·AI까지 가지 않고 422와 한국어 안내 한 문장으로 끝난다."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import api
from app import models as m
from app.auth import current_user


def no_agent():
    raise AssertionError("검증에 걸린 질문이 AI까지 갔다")


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(api, "qa_agent", no_agent)
    monkeypatch.setattr(api, "ask_limit", api.DailyLimit(5))
    api.app.dependency_overrides[current_user] = lambda: m.AppUser(id=0, email="o@t", role="owner")
    api.app.dependency_overrides[api.get_session] = lambda: None  # 검증에서 끝나므로 DB가 필요 없다
    try:
        yield TestClient(api.app)
    finally:
        api.app.dependency_overrides.clear()


@pytest.mark.parametrize("question", ["", " ", "     ", "\n\t ", "가", "  가  ", "가" * 301])
def test_bad_question_stops_before_ai_and_keeps_quota(owner, question):
    r = owner.post("/api/ask", json={"question": question})
    assert r.status_code == 422
    assert r.json()["detail"] == "질문은 2~300자로 입력해 주세요."
    assert api.ask_limit.used == 0  # 데모 하루 횟수도 깎이지 않는다


def test_question_is_trimmed_before_reaching_ai(owner, monkeypatch):
    seen = []

    class Agent:
        def ask(self, q):
            seen.append(q)
            return {"answer": "ok"}

    monkeypatch.setattr(api, "qa_agent", Agent)
    assert owner.post("/api/ask", json={"question": "  9월 합계?  "}).status_code == 200
    assert seen == ["9월 합계?"]
    assert api.ask_limit.used == 1 and api.ask_limit.day == datetime.now(api.KST).date()


@pytest.mark.parametrize(("body", "message"), [
    ({"email": "", "password": "x"}, "이메일을 확인해 주세요."),
    ({"email": "a" * 255, "password": "x"}, "이메일을 확인해 주세요."),
    ({"email": "o@t.kr", "password": ""}, "비밀번호를 입력해 주세요 (128자 이하)."),
    ({"email": "o@t.kr", "password": "x" * 129}, "비밀번호를 입력해 주세요 (128자 이하)."),
    ({"email": "o@t.kr"}, "비밀번호를 입력해 주세요 (128자 이하)."),
])
def test_bad_login_body_gets_korean_message(owner, body, message):
    r = owner.post("/api/auth/login", json=body)
    assert r.status_code == 422
    assert r.json()["detail"] == message
    assert r.json()["errors"]  # API를 직접 쓰는 쪽을 위한 원래 상세 목록


def test_bad_month_and_broken_json(owner):
    r = owner.get("/api/settlements", params={"month": "2026-13"})
    assert (r.status_code, r.json()["detail"]) == (422, "월은 2026-09처럼 YYYY-MM 형식이어야 합니다.")
    r = owner.post("/api/ask", content=b"{not json", headers={"content-type": "application/json"})
    assert (r.status_code, r.json()["detail"]) == (422, "요청 형식이 올바르지 않습니다.")

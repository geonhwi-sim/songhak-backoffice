"""질의 그래프의 흐름 (재시도, 포기, 답할 수 없는 질문). Claude 대신 가짜 클라이언트를 쓴다 (API 비용 없음).
SQL 실행은 실제 읽기 전용 계정으로 한다."""

import os
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import models as m
from app.qa.agent import Agent, SqlDraft
from app.qa.evaluate import answer_mentions
from app.qa.db import Result

pytestmark = pytest.mark.skipif(not os.environ.get("QA_DATABASE_URL"), reason="QA_DATABASE_URL 없음")

USAGE = NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)


class FakeClient:
    """SQL 초안을 순서대로 돌려주고, 답변 단계에서는 결과 첫 값을 그대로 말한다."""

    def __init__(self, drafts: list[SqlDraft]):
        self.drafts = list(drafts)
        self.sql_prompts: list[str] = []
        self.beta = NS(messages=NS(parse=self._parse, create=self._create))

    def _parse(self, **kw):
        self.sql_prompts.append(kw["messages"][0]["content"])
        return NS(stop_reason="end_turn", parsed_output=self.drafts.pop(0), usage=USAGE)

    def _create(self, **kw):
        body = kw["messages"][0]["content"]
        return NS(stop_reason="end_turn", usage=USAGE,
                  content=[NS(type="text", text="답: " + body.split("결과:\n")[1].splitlines()[1])])


def ask(drafts, question="질문"):
    client = FakeClient(drafts)
    return Agent(client=client).ask(question), client


def test_answers_on_first_try():
    out, _ = ask([SqlDraft(sql="SELECT 41 + 1 AS n", cannot_answer_reason=None)])
    assert (out["status"], out["answer"], out["rows"], len(out["attempts"])) == ("answered", "답: 42", [(42,)], 1)
    assert out["usage"]["calls"] == 2


def test_fixes_sql_after_db_error_and_shows_the_error_to_the_model():
    out, client = ask([
        SqlDraft(sql="SELECT no_such_column FROM places", cannot_answer_reason=None),
        SqlDraft(sql="SELECT count(*) FROM places", cannot_answer_reason=None),
    ])
    assert out["status"] == "answered" and len(out["attempts"]) == 2
    assert out["attempts"][0]["error"] and "no_such_column" in out["attempts"][0]["error"]
    assert "no_such_column" in client.sql_prompts[1]  # 두 번째 프롬프트에 오류가 들어간다


def test_gives_up_after_two_retries_without_guessing():
    bad = SqlDraft(sql="SELECT * FROM public.sales_orders", cannot_answer_reason=None)  # 권한 없음
    out, client = ask([bad, bad, bad, bad])
    assert out["status"] == "failed" and len(out["attempts"]) == 3  # 처음 1번 + 재시도 2번
    assert len(client.drafts) == 1 and out["rows"] == []
    assert "답을 찾지 못했습니다" in out["answer"]


def test_unanswerable_question_runs_no_sql():
    out, _ = ask([SqlDraft(sql=None, cannot_answer_reason="다음 달 매출은 데이터에 없습니다")])
    assert (out["status"], out["sql"], out["rows"]) == ("no_sql", None, [])
    assert "다음 달 매출" in out["answer"]


def test_answer_mentions_value():
    gold = Result(["sum"], [(3169700,)], False)
    assert answer_mentions(gold, "합계는 3,169,700원입니다.")
    assert answer_mentions(gold, "합계는 3,000,000원입니다.") is False
    assert answer_mentions(Result(["a", "b"], [("x", 1)], False), "x 1") is None


def test_ask_api_is_owner_only(monkeypatch):
    from app import api
    from app.auth import current_user

    monkeypatch.setattr(api, "qa_agent", lambda: Agent(client=FakeClient(
        [SqlDraft(sql="SELECT 7 AS n", cannot_answer_reason=None)])))
    c = TestClient(api.app)
    try:
        api.app.dependency_overrides[current_user] = lambda: m.AppUser(id=0, email="s@t", role="staff")
        assert c.post("/api/ask", json={"question": "몇 개야?"}).status_code == 403
        api.app.dependency_overrides[current_user] = lambda: m.AppUser(id=0, email="o@t", role="owner")
        body = c.post("/api/ask", json={"question": "몇 개야?"}).json()
        assert (body["answer"], body["sql"], body["rows"]) == ("답: 7", "SELECT 7 AS n", [[7]])
        assert "usage" not in body
        assert c.post("/api/ask", json={"question": ""}).status_code == 422
    finally:
        api.app.dependency_overrides.clear()

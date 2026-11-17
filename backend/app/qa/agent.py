"""자연어 질의 에이전트 (PRD A1).

질문 → SQL 작성 → 읽기 전용 계정으로 실행 → 답변
- SQL이 실패하면 오류를 보고 고쳐서 다시 쓴다. 재시도는 2회까지 (모두 3번 실행).
- 그래도 실패하거나, 뷰로 답할 수 없는 질문이면 "답을 찾지 못했다"고 말한다. 숫자를 추측하지 않는다.
- 답변은 실행 결과에 있는 값만 쓴다. 실행한 SQL과 결과 표를 함께 돌려준다.

안전장치는 app/qa/db.py (DB 권한)에 있다. 여기 프롬프트는 정답률을 위한 것이지 보안 장치가 아니다.
"""

import time
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Literal, TypedDict

import anthropic
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.qa.db import Result, qa_engine, run_sql

MODEL = "claude-opus-5-5"
MAX_RETRIES = 2
ANSWER_ROWS = 50  # 답변 단계에 보여 줄 행 수 (화면에는 전체 결과를 따로 보여 준다)


# ── 스키마 설명 (DB에서 읽는다: 뷰가 바뀌면 프롬프트도 따라 바뀐다) ──

DOMAIN_NOTES = """\
- 금액은 모두 원 단위 정수다.
- 이 식당은 정산처(회사)에 식대를 모아 청구한다. 정산 금액 = 매출 = sales_items.amount 합계 (모두 같은 뜻).
- 정산 월은 달력 기준(1일~말일)이다. "9월 정산" = sales_items.order_date가 9/1~9/30.
- 급여 월은 전월 17일 ~ 당월 16일이다. "10월 급여"는 payroll.month = '2026-10' (9/17~10/16).
  급여·인건비·주휴수당 질문은 payroll을 쓴다. 주휴수당을 직접 계산하지 않는다.
- "근무한 날", "근무 시간"처럼 달력 날짜로 묻는 질문은 work_logs를 쓴다.
- 발주 비용은 supply_orders.cost다. removed_from_app = true인 발주도 실제 발주이므로 빼지 않는다.
- 가게가 매장·도매상에서 직접 사 온 돈과 공과금·임대료는 expenses다 (발주와 따로). "발주 비용"은 supply_orders만,
  "지출"은 expenses만, "재료비"는 expenses의 food와 supply_orders.cost를 더한다.
- 연도가 없으면 오늘 날짜 기준으로 가장 최근의 그 달로 본다."""


@lru_cache
def schema_text() -> str:
    """qa 스키마의 뷰·열·설명을 읽어 프롬프트용 텍스트로 만든다."""
    sql = text("""
        SELECT c.table_name, c.column_name, c.data_type,
               obj_description(format('qa.%I', c.table_name)::regclass, 'pg_class') AS view_comment,
               col_description(format('qa.%I', c.table_name)::regclass, c.ordinal_position) AS col_comment
        FROM information_schema.columns c
        WHERE c.table_schema = 'qa'
        ORDER BY c.table_name, c.ordinal_position
    """)
    lines, current = [], None
    with qa_engine().connect() as conn:
        for view, col, typ, vcomment, ccomment in conn.execute(sql):
            if view != current:
                current = view
                lines.append(f"\n{view}" + (f"  -- {vcomment}" if vcomment else ""))
            lines.append(f"  {col} {typ}" + (f"  -- {ccomment}" if ccomment else ""))
    return "\n".join(lines).strip()


def sql_system_prompt() -> str:
    # 바뀌지 않는 내용만 둔다 (프롬프트 캐시). 오늘 날짜와 질문은 사용자 메시지로 보낸다.
    return f"""\
식당 백오피스의 PostgreSQL 데이터에 대한 사장님의 질문을 SQL로 바꾼다.

조회할 수 있는 뷰 (search_path = qa, 스키마 이름 없이 쓴다):
{schema_text()}

규칙:
- SELECT 한 문장만 쓴다. 위 뷰 말고는 조회할 수 없다.
- 질문이 묻는 값만 고른다. 이름과 값을 함께 물으면 둘 다 고른다.
- 상위 N개, 가장 큰 것을 물으면 ORDER BY와 LIMIT을 쓴다.
- 합계에 COALESCE(sum(...), 0)를 쓰지 않는다. 데이터가 없으면 NULL로 남겨야 "데이터 없음"과 "0원"을 구분할 수 있다.
- 위 뷰로 답할 수 없는 질문(예측, 데이터에 없는 정보, 데이터 변경 요청)이면 sql을 비우고 이유를 쓴다.

도메인 규칙:
{DOMAIN_NOTES}"""


ANSWER_SYSTEM = """\
식당 사장님의 질문에, 실행한 SQL 결과만 보고 한국어로 짧게 답한다.
- 결과에 있는 숫자만 쓴다. 계산이 필요하면 결과의 숫자로만 하고, 추측하지 않는다.
- 금액은 "1,234,000원"처럼 쓴다.
- 결과가 비었거나 값이 모두 NULL이면 "해당 기간의 데이터가 없습니다"라고 답한다.
- 한두 문장으로 답한다. 표는 화면에 따로 보여 주므로 결과를 모두 옮겨 적지 않는다.
- 결과 값이 곧 질문의 답이다. "실제와 다를 수 있다" 같은 단서, 데이터에 없는 사정(수수료, 세금, 원인 추정)을
  덧붙이지 않는다. 테이블·열 이름 같은 내부 용어를 쓰지 않는다. 마크다운 강조(**)는 쓰지 않는다."""


class SqlDraft(BaseModel):
    sql: str | None = Field(description="실행할 SELECT 문. 답할 수 없으면 null")
    cannot_answer_reason: str | None = Field(description="sql이 null일 때만, 답할 수 없는 이유")


# ── 그래프 ──


class Attempt(TypedDict):
    sql: str
    error: str | None


class State(TypedDict, total=False):
    question: str
    today: str
    attempts: list[Attempt]
    result: Result | None
    answer: str
    status: Literal["answered", "no_sql", "failed", "refused"]
    usage: dict


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    calls: int = 0

    def add(self, u) -> None:
        self.calls += 1
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            setattr(self, k, getattr(self, k) + (getattr(u, k, 0) or 0))


@dataclass
class Agent:
    client: anthropic.Anthropic = field(default_factory=lambda: anthropic.Anthropic(max_retries=3))
    model: str = MODEL
    usage: Usage = field(default_factory=Usage)

    def _call(self, **kwargs):
        # 안전 분류기가 거절하면 서버가 권장 모델로 다시 실행한다 (fallbacks: "default")
        r = self.client.beta.messages.parse(
            model=self.model, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            cache_control={"type": "ephemeral"}, **kwargs,
        )
        self.usage.add(r.usage)
        return r

    # 노드: SQL 쓰기 (처음, 또는 앞 시도의 오류를 보고 고치기)
    def write_sql(self, state: State) -> State:
        content = f"오늘: {state['today']}\n질문: {state['question']}"
        for a in state.get("attempts", []):
            content += f"\n\n앞에서 쓴 SQL:\n{a['sql']}\n실행 오류:\n{a['error']}\n오류를 보고 고친 SQL을 다시 써라."
        r = self._call(
            max_tokens=4000, system=sql_system_prompt(), output_config={"effort": "medium"},
            messages=[{"role": "user", "content": content}], output_format=SqlDraft,
        )
        if r.stop_reason == "refusal":
            return {"status": "refused", "answer": "이 질문에는 답할 수 없습니다."}
        draft: SqlDraft = r.parsed_output
        if not draft.sql:
            return {"status": "no_sql", "answer": f"답을 찾지 못했습니다. {draft.cannot_answer_reason or ''}".strip()}
        return {"attempts": [*state.get("attempts", []), {"sql": draft.sql.strip(), "error": None}]}

    # 노드: 실행
    def execute(self, state: State) -> State:
        attempts = list(state["attempts"])
        try:
            return {"result": run_sql(attempts[-1]["sql"])}
        except Exception as e:  # noqa: BLE001  DB 오류는 에이전트가 읽고 고친다
            attempts[-1] = {**attempts[-1], "error": str(getattr(e, "orig", e)).strip().splitlines()[0][:500]}
            return {"attempts": attempts, "result": None}

    # 노드: 답변
    def answer(self, state: State) -> State:
        res = state["result"]
        table = "\n".join(["\t".join(res.columns)] + ["\t".join(map(str, row)) for row in res.rows[:ANSWER_ROWS]])
        more = f"\n(모두 {len(res.rows)}행 중 {ANSWER_ROWS}행만 보임)" if len(res.rows) > ANSWER_ROWS else ""
        r = self.client.beta.messages.create(
            model=self.model, max_tokens=2000, system=f"{ANSWER_SYSTEM}\n\n도메인 규칙:\n{DOMAIN_NOTES}",
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            messages=[{"role": "user", "content":
                       f"질문: {state['question']}\n\n실행한 SQL:\n{state['attempts'][-1]['sql']}\n\n결과:\n{table}{more}"}],
        )
        self.usage.add(r.usage)
        if r.stop_reason == "refusal":
            return {"status": "refused", "answer": "이 질문에는 답할 수 없습니다."}
        text_out = "".join(b.text for b in r.content if b.type == "text").strip()
        return {"status": "answered", "answer": text_out}

    def give_up(self, state: State) -> State:
        return {"status": "failed", "answer": "답을 찾지 못했습니다. SQL을 여러 번 고쳐 봤지만 실행에 실패했습니다."}

    def build(self):
        g = StateGraph(State)
        g.add_node("write_sql", self.write_sql)
        g.add_node("execute", self.execute)
        g.add_node("answer", self.answer)
        g.add_node("give_up", self.give_up)
        g.add_edge(START, "write_sql")
        g.add_conditional_edges("write_sql", lambda s: END if s.get("status") else "execute")
        g.add_conditional_edges("execute", after_execute)
        g.add_edge("answer", END)
        g.add_edge("give_up", END)
        return g.compile()

    def ask(self, question: str, today: date | None = None) -> dict:
        from app.reports import today_kst

        start = time.monotonic()
        before = Usage(**vars(self.usage))
        out = self.build().invoke({"question": question, "today": (today or today_kst()).isoformat(), "attempts": []})
        res: Result | None = out.get("result")
        return {
            "question": question,
            "status": out["status"],
            "answer": out["answer"],
            "sql": out["attempts"][-1]["sql"] if out.get("attempts") else None,
            "attempts": out.get("attempts", []),
            "columns": res.columns if res else [],
            "rows": res.rows if res else [],
            "truncated": res.truncated if res else False,
            "seconds": round(time.monotonic() - start, 1),
            "usage": {k: getattr(self.usage, k) - getattr(before, k) for k in vars(before)},
        }


def after_execute(state: State) -> str:
    last = state["attempts"][-1]
    if last["error"] is None:
        return "answer"
    return "write_sql" if len(state["attempts"]) <= MAX_RETRIES else "give_up"

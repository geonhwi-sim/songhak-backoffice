"""읽기 전용 계정으로 SQL을 실행한다.

안전장치는 프롬프트가 아니라 DB가 막는다 (docs/decisions.md 2026-11-02).
- 계정 권한: qa 스키마 뷰의 SELECT만 (qa_readonly 역할)
- 계정 기본값: 읽기 전용 트랜잭션, 실행 시간 5초, search_path = qa
- 여기서 한 번 더: 트랜잭션을 READ ONLY로 열고 항상 되돌린다. 반환 행 수는 MAX_ROWS까지만.
"""

import os
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import db as _db  # noqa: F401  .env를 읽게 한다

MAX_ROWS = 200
TIMEOUT = "5s"


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple]
    truncated: bool  # MAX_ROWS보다 많아서 잘렸는지


@lru_cache
def qa_engine() -> Engine:
    url = os.environ.get("QA_DATABASE_URL")
    if not url:
        raise RuntimeError("QA_DATABASE_URL이 없습니다. python -m app.qa.create_user 로 계정을 만드세요.")
    return create_engine(url, pool_pre_ping=True)


def run_sql(sql: str, engine: Engine | None = None) -> Result:
    """SQL 한 문장을 실행한다. 실패하면 DB 오류를 그대로 올린다 (에이전트가 보고 고친다)."""
    engine = engine or qa_engine()
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text(f"SET LOCAL statement_timeout = '{TIMEOUT}'"))
        try:
            cur = conn.exec_driver_sql(sql.strip().rstrip(";"))
            rows = [tuple(r) for r in cur.fetchmany(MAX_ROWS + 1)]
            return Result(list(cur.keys()), rows[:MAX_ROWS], len(rows) > MAX_ROWS)
        finally:
            conn.rollback()

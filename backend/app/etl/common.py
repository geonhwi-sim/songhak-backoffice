"""가져오기 스크립트 공통: upsert와 실행 기록(sync_runs)."""

import json
from collections.abc import Callable
from time import perf_counter

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import models as m
from app.db import SessionLocal


def upsert(session: Session, model, rows: list[dict], key: str = "source_id") -> dict:
    """key 기준으로 넣거나 고치고, {key 값: id}를 돌려준다. id가 없는 테이블은 빈 dict."""
    if not rows:
        return {}
    stmt = insert(model).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[key], set_={c: stmt.excluded[c] for c in rows[0] if c != key}
    )
    if not hasattr(model, "id"):
        session.execute(stmt)
        return {}
    return dict(session.execute(stmt.returning(getattr(model, key), model.id)).all())


def run_recorded(source: str, job: Callable[[Session], dict]) -> dict:
    """job을 트랜잭션 하나로 실행하고 sync_runs에 결과를 남긴다.
    실행 기록은 별도 트랜잭션이라 job이 실패해도 남는다."""
    if SessionLocal is None:
        raise SystemExit("DATABASE_URL이 없습니다. .env를 확인하세요.")
    with SessionLocal.begin() as s:
        run = m.SyncRun(source=source)
        s.add(run)
        s.flush()
        run_id = run.id

    def finish(status: str, stats: dict | None, error: str | None) -> None:
        with SessionLocal.begin() as s:
            s.execute(update(m.SyncRun).where(m.SyncRun.id == run_id).values(
                status=status, stats=stats, error=error, finished_at=func.now()))

    started = perf_counter()
    try:
        with SessionLocal.begin() as s:
            stats = job(s)
    except Exception as e:
        finish("failed", None, repr(e))
        raise
    stats["seconds"] = round(perf_counter() - started, 2)
    finish("succeeded", stats, None)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return stats

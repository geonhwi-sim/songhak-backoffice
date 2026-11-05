"""API 공통 의존성."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import SessionLocal


def get_session() -> Iterator[Session]:
    if SessionLocal is None:
        raise HTTPException(503, "DATABASE_URL이 설정되지 않았습니다")
    with SessionLocal() as s:
        yield s


DB = Annotated[Session, Depends(get_session)]

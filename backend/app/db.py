import os
from pathlib import Path

from dotenv import load_dotenv
from datetime import datetime

from sqlalchemy import DateTime, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 저장소 루트의 .env를 읽는다 (docker-compose와 같은 파일)
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# 제약 조건 이름을 일정하게 붙여, 마이그레이션에서 이름으로 찾을 수 있게 한다
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # 시각은 모두 timestamptz로 저장한다 (Firebase 시각은 UTC, 화면은 KST)
    type_annotation_map = {datetime: DateTime(timezone=True)}


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL이 없습니다. .env.example을 복사해 .env를 만드세요.")
    return url


engine = create_engine(database_url(), pool_pre_ping=True) if os.environ.get("DATABASE_URL") else None
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False) if engine else None

"""앱 DB 계정: 데이터 읽기·쓰기는 되고, 스키마·권한·마이그레이션 기록은 못 바꾼다."""

import secrets

import psycopg
import pytest

from app.db import engine

pytestmark = pytest.mark.skipif(engine is None, reason="DATABASE_URL 없음")

ROLE = "songhak_app_test"


@pytest.fixture(scope="module")
def app_conn():
    from app.app_user import setup_app_role

    password = secrets.token_urlsafe(24)
    url = engine.url
    with engine.begin() as conn:
        setup_app_role(conn.connection.cursor(), ROLE, password, url.database, url.username)
    conn = psycopg.connect(host=url.host, port=url.port or 5432, dbname=url.database, user=ROLE, password=password)
    yield conn
    conn.close()
    with engine.begin() as c:
        cur = c.connection.cursor()
        cur.execute(f"DROP OWNED BY {ROLE}")
        cur.execute(f"DROP ROLE {ROLE}")


def run(conn, statement):
    """한 문장을 실행하고 늘 되돌린다 (테스트가 DB를 바꾸지 않게)."""
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
            return cur.fetchall() if cur.description else None
    finally:
        conn.rollback()


def test_app_account_reads_and_writes_data(app_conn):
    assert run(app_conn, "SELECT count(*) FROM places")[0][0] >= 0
    run(app_conn, "INSERT INTO day_memos(memo_date, body) VALUES ('2099-01-01', 'x')")
    run(app_conn, "UPDATE places SET name = name WHERE false")
    run(app_conn, "DELETE FROM day_memos WHERE memo_date = '2099-01-01'")


def test_app_account_is_not_superuser(app_conn):
    assert run(app_conn, "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user") == [
        (False, False, False)
    ]


@pytest.mark.parametrize("statement", [
    "CREATE TABLE evil(x int)",
    "DROP TABLE places",
    "ALTER TABLE places ADD COLUMN x int",
    "TRUNCATE places CASCADE",
    "CREATE ROLE evil",
    "UPDATE alembic_version SET version_num = 'x'",
    f"ALTER TABLE places OWNER TO {ROLE}",
])
def test_app_account_cannot_change_schema_or_permissions(app_conn, statement):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        run(app_conn, statement)

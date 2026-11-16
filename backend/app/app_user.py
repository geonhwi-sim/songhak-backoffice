"""앱(API·동기화)이 접속할 DB 계정을 만든다 (이미 있으면 비밀번호와 권한만 다시 맞춘다).

사용법 (backend 폴더): python -m app.app_user
- APP_DATABASE_URL에 적은 사용자 이름·비밀번호로 만든다. 비밀번호는 저장소에 두지 않는다.
- DATABASE_URL(테이블 주인인 관리 계정)로 실행한다. 운영에서는 compose의 migrate 서비스가 켤 때마다 실행한다.

앱 계정이 할 수 있는 것: public 스키마 테이블의 데이터 읽기·쓰기(SELECT, INSERT, UPDATE, DELETE), 시퀀스 사용.
할 수 없는 것: 테이블 만들기·바꾸기·지우기, TRUNCATE, 계정 만들기, 마이그레이션 기록(alembic_version) 수정.
그래서 앱 코드나 SQL 주입이 뚫려도 스키마를 망가뜨리거나 권한을 넓힐 수 없다.
"""

import os

from psycopg import sql
from sqlalchemy.engine import make_url

ROLE_OPTIONS = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
DML = "SELECT, INSERT, UPDATE, DELETE"


def setup_app_role(cur, username: str, password: str, database: str, owner: str) -> str:
    """cur: 관리 계정(테이블 주인)으로 연 psycopg 커서. 만들었으면 CREATE, 있던 계정이면 ALTER를 돌려준다."""
    user = sql.Identifier(username)
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (username,))
    verb = "ALTER" if cur.fetchone() else "CREATE"
    cur.execute(sql.SQL(verb + " ROLE {} " + ROLE_OPTIONS + " PASSWORD {}").format(user, sql.Literal(password)))
    cur.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '30s'").format(user))

    cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), user))
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(user))
    cur.execute(sql.SQL("GRANT " + DML + " ON ALL TABLES IN SCHEMA public TO {}").format(user))
    cur.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(user))
    # 앞으로 마이그레이션이 만드는 테이블에도 같은 권한이 붙게 한다
    cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT " + DML + " ON TABLES TO {}")
                .format(sql.Identifier(owner), user))
    cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}")
                .format(sql.Identifier(owner), user))
    # 마이그레이션 기록은 관리 계정만 바꾼다
    cur.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL")
    if cur.fetchone()[0]:
        cur.execute(sql.SQL("REVOKE INSERT, UPDATE, DELETE ON public.alembic_version FROM {}").format(user))
    return verb


def main() -> None:
    from app.db import engine

    url = os.environ.get("APP_DATABASE_URL")
    if not url or engine is None:
        raise SystemExit("DATABASE_URL(관리 계정)과 APP_DATABASE_URL을 설정하세요")
    app = make_url(url)
    if not app.username or not app.password:
        raise SystemExit("APP_DATABASE_URL에 사용자 이름과 비밀번호가 있어야 합니다")
    if app.username == engine.url.username:
        raise SystemExit("APP_DATABASE_URL은 관리 계정과 다른 사용자여야 합니다")

    with engine.begin() as conn:
        cur = conn.connection.cursor()
        verb = setup_app_role(cur, app.username, app.password, app.database, engine.url.username)
    print(f"{verb} {app.username}: 데이터 읽기·쓰기만")


if __name__ == "__main__":
    main()

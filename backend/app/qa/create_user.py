"""자연어 질의용 로그인 계정을 만든다 (이미 있으면 비밀번호와 설정만 다시 맞춘다).

사용법 (backend 폴더): python -m app.qa.create_user
- .env의 QA_DATABASE_URL에 적은 사용자 이름·비밀번호로 만든다. 비밀번호는 저장소에 두지 않는다.
- DATABASE_URL(관리 계정)로 실행한다. qa_readonly 역할은 마이그레이션이 만든다.
"""

import os

from psycopg import sql
from sqlalchemy.engine import make_url

from app.db import engine


def main() -> None:
    url = os.environ.get("QA_DATABASE_URL")
    if not url or engine is None:
        raise SystemExit("DATABASE_URL과 QA_DATABASE_URL을 .env에 설정하세요")
    qa = make_url(url)
    if not qa.username or not qa.password:
        raise SystemExit("QA_DATABASE_URL에 사용자 이름과 비밀번호가 있어야 합니다")
    user = sql.Identifier(qa.username)

    with engine.begin() as conn:
        cur = conn.connection.cursor()
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (qa.username,))
        verb = "ALTER" if cur.fetchone() else "CREATE"
        cur.execute(sql.SQL(verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}")
                    .format(user, sql.Literal(qa.password)))
        cur.execute(sql.SQL("GRANT qa_readonly TO {}").format(user))
        for setting, value in (("default_transaction_read_only", "on"), ("statement_timeout", "5s"),
                               ("search_path", "qa")):
            cur.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(user, sql.Identifier(setting), sql.Literal(value)))
        # 읽기 전용 트랜잭션에서도 임시 테이블은 만들 수 있으므로 막는다 (관리 계정은 DB 소유자라 영향 없음)
        cur.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(sql.Identifier(qa.database)))
    print(f"{verb} {qa.username}: qa 스키마 읽기 전용")


if __name__ == "__main__":
    main()

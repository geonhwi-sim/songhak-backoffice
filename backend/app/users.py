"""백오피스 계정 관리.

사용법 (backend 폴더)
  python -m app.users add 이메일 --role owner      비밀번호는 화면에 보이지 않게 두 번 입력
  python -m app.users passwd 이메일                비밀번호 바꾸기
  python -m app.users disable 이메일               로그인 막기 (지우지 않는다)
  python -m app.users list

자동화(데모 계정 등)에서는 --password-env 변수이름 으로 환경 변수에서 비밀번호를 읽는다.
"""

import argparse
import getpass
import os

from sqlalchemy import select

from app import models as m
from app.auth import hash_password
from app.db import SessionLocal

MIN_LENGTH = 10
MAX_LENGTH = 128  # 로그인 API의 비밀번호 상한(app.api.PASSWORD_MAX)과 같게


def read_password(env: str | None) -> str:
    if env:
        pw = os.environ.get(env, "")
    else:
        pw = getpass.getpass("비밀번호: ")
        if pw != getpass.getpass("한 번 더: "):
            raise SystemExit("두 비밀번호가 다릅니다")
    if len(pw) < MIN_LENGTH:
        raise SystemExit(f"비밀번호는 {MIN_LENGTH}자 이상이어야 합니다")
    if len(pw) > MAX_LENGTH:
        raise SystemExit(f"비밀번호는 {MAX_LENGTH}자 이하여야 합니다")
    return pw


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="백오피스 계정 관리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("email")
    add.add_argument("--role", choices=["owner", "staff"], required=True)
    add.add_argument("--password-env")
    pw = sub.add_parser("passwd")
    pw.add_argument("email")
    pw.add_argument("--password-env")
    sub.add_parser("disable").add_argument("email")
    sub.add_parser("list")
    args = ap.parse_args(argv)

    if SessionLocal is None:
        raise SystemExit("DATABASE_URL이 없습니다")
    with SessionLocal() as s:
        if args.cmd == "list":
            for u in s.scalars(select(m.AppUser).order_by(m.AppUser.id)):
                print(u.id, u.email, u.role, "사용" if u.is_active else "막힘", u.last_login_at or "-")
            return
        email = args.email.strip().lower()
        user = s.scalar(select(m.AppUser).where(m.AppUser.email == email))
        if args.cmd == "add":
            if user:
                raise SystemExit("이미 있는 이메일입니다")
            s.add(m.AppUser(email=email, role=args.role, password_hash=hash_password(read_password(args.password_env))))
        elif user is None:
            raise SystemExit("없는 이메일입니다")
        elif args.cmd == "passwd":
            user.password_hash, user.is_active = hash_password(read_password(args.password_env)), True
        else:
            user.is_active = False
        s.commit()
        print(f"{args.cmd} {email}: 완료")


if __name__ == "__main__":
    main()

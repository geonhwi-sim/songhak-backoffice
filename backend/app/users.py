"""백오피스 계정 관리.

사용법 (backend 폴더)
  python -m app.users add 이메일 --role owner                  비밀번호는 화면에 보이지 않게 두 번 입력
  python -m app.users add 이메일 --role staff --employee 3      직원 계정: 직원 번호는 employees로 확인
  python -m app.users employees                               직원 번호·이름·연결된 계정
  python -m app.users passwd 이메일                            비밀번호 바꾸기
  python -m app.users disable 이메일                           로그인 막기 (지우지 않는다)
  python -m app.users list

자동화(데모 계정 등)에서는 --password-env 변수이름 으로 환경 변수에서 비밀번호를 읽는다.
직원 계정은 본인 근무 기록과 예상 급여(/api/me/pay)만 볼 수 있다.
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


def check_employee(s, role: str, employee_id: int | None) -> int | None:
    """직원 계정은 있는 직원 한 명과 연결해야 하고, 사장님 계정은 연결하지 않는다 (DB 제약과 같은 규칙)."""
    if role == "owner":
        if employee_id is not None:
            raise SystemExit("사장님 계정에는 --employee를 쓰지 않습니다")
        return None
    if employee_id is None:
        raise SystemExit("직원 계정은 --employee 직원번호가 필요합니다 (python -m app.users employees)")
    if s.get(m.Employee, employee_id) is None:
        raise SystemExit(f"직원 {employee_id}번이 없습니다")
    if s.scalar(select(m.AppUser.email).where(m.AppUser.employee_id == employee_id)):
        raise SystemExit(f"직원 {employee_id}번에는 이미 계정이 있습니다")
    return employee_id


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="백오피스 계정 관리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("email")
    add.add_argument("--role", choices=["owner", "staff"], required=True)
    add.add_argument("--employee", type=int, help="직원 계정이 볼 직원 번호")
    add.add_argument("--password-env")
    pw = sub.add_parser("passwd")
    pw.add_argument("email")
    pw.add_argument("--password-env")
    sub.add_parser("disable").add_argument("email")
    sub.add_parser("list")
    sub.add_parser("employees")
    args = ap.parse_args(argv)

    if SessionLocal is None:
        raise SystemExit("DATABASE_URL이 없습니다")
    with SessionLocal() as s:
        if args.cmd == "list":
            for u in s.scalars(select(m.AppUser).order_by(m.AppUser.id)):
                who = f"직원 {u.employee_id}번" if u.employee_id else "-"
                print(u.id, u.email, u.role, who, "사용" if u.is_active else "막힘", u.last_login_at or "-")
            return
        if args.cmd == "employees":
            accounts = dict(s.execute(select(m.AppUser.employee_id, m.AppUser.email)
                                      .where(m.AppUser.employee_id.is_not(None))).all())
            for e in s.scalars(select(m.Employee).order_by(m.Employee.id)):
                print(e.id, e.name, "재직" if e.is_active else "퇴사", accounts.get(e.id, "계정 없음"))
            return
        email = args.email.strip().lower()
        user = s.scalar(select(m.AppUser).where(m.AppUser.email == email))
        if args.cmd == "add":
            if user:
                raise SystemExit("이미 있는 이메일입니다")
            employee_id = check_employee(s, args.role, args.employee)
            s.add(m.AppUser(email=email, role=args.role, employee_id=employee_id,
                            password_hash=hash_password(read_password(args.password_env))))
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

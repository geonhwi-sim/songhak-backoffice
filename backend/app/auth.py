"""로그인과 권한 (PRD U1, docs/decisions.md 2026-11-02).

- 비밀번호: scrypt 해시 (표준 라이브러리). 형식 scrypt$n$r$p$salt$hash
- 로그인하면 서명한 토큰(JWT, 12시간)을 HttpOnly 쿠키로 준다. 화면 코드(JavaScript)는 토큰을 읽을 수 없다.
- 권한은 API 의존성으로 검사한다: current_user(로그인 필요), require_owner(사장님만).
- 같은 이메일로 5번 틀리면 10분 동안 막는다 (서버 메모리 기준, 서버 1대 전제).
"""

import base64
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Cookie, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app.deps import DB

COOKIE = "songhak_session"
TOKEN_HOURS = 12
MAX_FAILS = 5
LOCK_SECONDS = 600
SCRYPT = {"n": 2**14, "r": 8, "p": 1}


# ── 비밀번호 ──


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except ValueError:
        return False


# 사용자가 없을 때도 같은 시간이 걸리게 해서, 응답 시간으로 가입된 이메일을 알아낼 수 없게 한다
_DUMMY_HASH = hash_password(secrets.token_hex(8))


# ── 토큰 ──


def secret() -> str:
    s = os.environ.get("AUTH_SECRET")
    if not s or len(s) < 32:
        raise HTTPException(503, "AUTH_SECRET(32자 이상)이 설정되지 않았습니다")
    return s


def make_token(user: m.AppUser) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": str(user.id), "role": user.role, "iat": now, "exp": now + timedelta(hours=TOKEN_HOURS)}
    return jwt.encode(claims, secret(), algorithm="HS256")


def read_token(token: str) -> int | None:
    try:
        return int(jwt.decode(token, secret(), algorithms=["HS256"])["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def cookie_options() -> dict:
    # 로컬 개발(http)에서만 COOKIE_SECURE=0으로 끈다. 배포 환경은 HTTPS라 기본값 그대로.
    secure = os.environ.get("COOKIE_SECURE", "1") != "0"
    return {"httponly": True, "secure": secure, "samesite": "lax", "max_age": TOKEN_HOURS * 3600, "path": "/"}


# ── 로그인 실패 제한 ──

_fails: dict[str, list[float]] = {}


def is_locked(email: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _fails.get(email, []) if now - t < LOCK_SECONDS]
    _fails[email] = recent
    return len(recent) >= MAX_FAILS


def record_fail(email: str) -> None:
    _fails.setdefault(email, []).append(time.monotonic())


def authenticate(session: Session, email: str, password: str) -> m.AppUser:
    email = email.strip().lower()
    if is_locked(email):
        raise HTTPException(429, "로그인 시도가 너무 많습니다. 10분 뒤에 다시 시도하세요")
    user = session.scalar(select(m.AppUser).where(m.AppUser.email == email))
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH) and user is not None
    if not ok or not user.is_active:
        record_fail(email)
        raise HTTPException(401, "이메일 또는 비밀번호가 맞지 않습니다")
    _fails.pop(email, None)
    user.last_login_at = datetime.now(timezone.utc)
    session.commit()
    return user


# ── API 의존성 ──


def current_user(s: DB, token: Annotated[str | None, Cookie(alias=COOKIE)] = None) -> m.AppUser:
    user_id = read_token(token) if token else None
    user = s.get(m.AppUser, user_id) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(401, "로그인이 필요합니다")
    return user


def require_owner(user: Annotated[m.AppUser, Depends(current_user)]) -> m.AppUser:
    if user.role != "owner":
        raise HTTPException(403, "사장님 계정만 볼 수 있습니다")
    return user


User = Annotated[m.AppUser, Depends(current_user)]
Owner = Annotated[m.AppUser, Depends(require_owner)]

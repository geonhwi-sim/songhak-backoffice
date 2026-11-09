"""공개 데모 환경. 가짜 데이터(seed.py)와 데모 계정."""

import os

DEMO_EMAIL = "demo@example.com"


def demo_mode() -> bool:
    """데모 서버에서만 DEMO_MODE=1. 운영 서버에서는 데모 로그인이 열리지 않는다."""
    return os.environ.get("DEMO_MODE") == "1"

"""공개 데모 환경. 가짜 데이터(seed.py)와 데모 계정."""

import os

DEMO_EMAIL = "demo@example.com"
DEMO_STAFF_EMAIL = "demo-staff@example.com"  # 데모 직원(첫 번째 직원)과 연결된 직원 계정


def demo_mode() -> bool:
    """데모 서버에서만 DEMO_MODE=1. 운영 서버에서는 데모 로그인이 열리지 않는다."""
    return os.environ.get("DEMO_MODE") == "1"

# FastAPI 서버 이미지 (운영용). docker-compose.prod.yml이 빌드한다.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 의존성을 먼저 깔아 두면 코드만 바뀔 때 이 단계는 캐시를 쓴다
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

# root가 아닌 사용자로 실행한다
RUN useradd --system --uid 10001 --no-create-home app
USER app

EXPOSE 8000

# 켤 때마다: 마이그레이션 → 질의용 읽기 전용 계정 맞추기 → 서버 실행 (둘 다 여러 번 돌려도 안전)
CMD ["sh", "-c", "alembic upgrade head && python -m app.qa.create_user && exec uvicorn app.api:app --host 0.0.0.0 --port 8000"]

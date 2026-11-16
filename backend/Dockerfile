# FastAPI 서버 이미지 (운영용). docker-compose.prod.yml이 빌드한다.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 정산서 PDF용 한글 글꼴 (나눔고딕)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-nanum && rm -rf /var/lib/apt/lists/*

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

# 서버만 실행한다. 마이그레이션과 계정 맞추기는 관리 계정을 가진 migrate 서비스가 먼저 한다 (docker-compose.prod.yml)
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]

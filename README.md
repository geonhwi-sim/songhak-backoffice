# 송학구이 통합 백오피스

식당의 발주 앱과 정산 앱 데이터를 Postgres로 모아, 대시보드·자연어 질의·월말 정산 자동화를 제공하는 관리자 도구입니다.

- 기획: [docs/PRD.md](docs/PRD.md)
- 데이터 설계: [docs/erd.md](docs/erd.md)
- 결정 기록: [docs/decisions.md](docs/decisions.md)

개발 중입니다.

## 로컬 실행

필요한 것: Docker Desktop, Python 3.13

```bash
cp .env.example .env              # 비밀번호를 채운다 (DATABASE_URL, QA_DATABASE_URL)
docker compose up -d db           # Postgres 17

cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # macOS/Linux: .venv/bin/pip
.venv/Scripts/alembic upgrade head                  # 테이블, 질의용 qa 뷰 생성
.venv/Scripts/python -m app.qa.create_user          # 자연어 질의용 읽기 전용 계정
.venv/Scripts/python -m pytest
```

자연어 질의는 `qa` 스키마의 뷰만 읽을 수 있는 계정으로 SQL을 실행합니다. 원본 테이블 조회·쓰기·임시 테이블·5초 넘는 쿼리는 DB가 막습니다.
평가 세트는 [backend/eval/questions.yaml](backend/eval/questions.yaml) (20문항), 정답 SQL 확인은 `python -m app.qa.evaluate --gold`.

스키마를 바꿀 때: `app/models.py`를 고친 뒤 `alembic revision --autogenerate -m "설명"` → 생성된 파일 검토 → `alembic upgrade head` → `pytest` → `alembic check`로 모델과 DB가 일치하는지 확인한다.
CHECK 제약은 컬럼이 아니라 테이블 단위(`__table_args__`, `__checks__`)로 둔다. 컬럼에 붙이면 autogenerate가 빠뜨린다.

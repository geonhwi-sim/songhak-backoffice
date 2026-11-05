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
.venv/Scripts/python -m app.users add 이메일 --role owner   # 로그인 계정 (비밀번호는 입력 창에)
.venv/Scripts/python -m pytest
.venv/Scripts/uvicorn app.api:app --reload          # http://127.0.0.1:8000/docs
```

로그인하면 서명한 토큰을 HttpOnly 쿠키로 받습니다. `/api/health`와 로그인을 빼면 모든 API에 로그인이 필요하고, 조회 API는 사장님(owner) 계정만 쓸 수 있습니다. 같은 이메일로 5번 틀리면 10분 동안 막힙니다.

자연어 질의는 `qa` 스키마의 뷰만 읽을 수 있는 계정으로 SQL을 실행합니다. 원본 테이블 조회·쓰기·임시 테이블·5초 넘는 쿼리는 DB가 막습니다.
질문 → SQL 작성 → 실행 → (실패하면 오류를 보고 고쳐서 2회까지 재시도) → 답변. `POST /api/ask` (사장님 계정). `.env`에 `ANTHROPIC_API_KEY`가 필요합니다.
평가 세트는 [backend/eval/questions.yaml](backend/eval/questions.yaml) (20문항). 정답 SQL 확인은 `python -m app.qa.evaluate --gold`, 에이전트 채점은 `--run` (API 비용 발생, 결과는 `data/eval_runs/`). 2026-11-05 기준 20/20.

스키마를 바꿀 때: `app/models.py`를 고친 뒤 `alembic revision --autogenerate -m "설명"` → 생성된 파일 검토 → `alembic upgrade head` → `pytest` → `alembic check`로 모델과 DB가 일치하는지 확인한다.
CHECK 제약은 컬럼이 아니라 테이블 단위(`__table_args__`, `__checks__`)로 둔다. 컬럼에 붙이면 autogenerate가 빠뜨린다.

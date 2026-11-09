# 배포 (AWS EC2)

```
인터넷 ─443─▶ nginx ──▶ web (Next.js :3000) ──/api/*──▶ api (FastAPI :8000) ──▶ db (Postgres :5432)
          80 → 443으로 보냄          certbot: 12시간마다 인증서 갱신 확인
```

- 설정 파일: [docker-compose.prod.yml](../docker-compose.prod.yml), [deploy/nginx/templates/default.conf.template](../deploy/nginx/templates/default.conf.template)
- 바깥에 열리는 포트는 nginx의 80·443뿐이다. DB·API는 compose 내부 네트워크에서만 접속할 수 있다.
- api 컨테이너는 켤 때마다 `alembic upgrade head`와 질의용 읽기 전용 계정 설정을 실행한다. 둘 다 여러 번 실행해도 안전하다.
- 로그인 쿠키는 `Secure`(HTTPS 전용)로 고정된다. 그래서 HTTPS 없이는 로그인이 되지 않는다.

## 1. AWS

| 항목 | 값 |
|---|---|
| 리전 | 서울 (ap-northeast-2) |
| 인스턴스 | t4g.small (Arm, 메모리 2GB), Ubuntu Server 24.04 LTS |
| 스토리지 | gp3 20GB |
| 보안 그룹 | 22: 내 IP만 / 80, 443: 전체 |
| 고정 IP | 탄력적 IP를 인스턴스에 연결 (서버를 지울 때 IP도 릴리스) |

## 2. 서버 준비 (처음 한 번)

```bash
ssh -i ~/.ssh/songhak-backoffice.pem ubuntu@<탄력적 IP>

# 스왑 2GB: 이미지 빌드(Next.js) 중 메모리 부족으로 멈추지 않게
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Docker (공식 저장소)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu   # 로그아웃 후 다시 접속하면 sudo 없이 docker 사용

# 코드
git clone https://github.com/geonhwi-sim/songhak-backoffice.git
cd songhak-backoffice
```

## 3. .env (서버에만 둔다)

```bash
cp .env.example .env && nano .env
```

운영에서 쓰는 값만 채우면 된다. `DATABASE_URL`, `QA_DATABASE_URL`, `COOKIE_SECURE`는 compose가 직접 정하므로 서버 `.env`에서는 무시된다.

| 이름 | 값 |
|---|---|
| `POSTGRES_PASSWORD` | 무작위 문자열 |
| `QA_DB_PASSWORD` | 무작위 문자열 (질의용 읽기 전용 계정) |
| `AUTH_SECRET` | 무작위 문자열 48자 이상 |
| `ANTHROPIC_API_KEY` | Claude API 키 (자연어 질의) |
| `DOMAIN` | 무료 주소 `<IP의 점을 -로>.sslip.io` (예: IP 3.38.1.2 → `3-38-1-2.sslip.io`). 도메인을 사면 이 값만 바꾸고 인증서를 새로 받는다 |
| `CERTBOT_EMAIL` | 인증서 만료 알림을 받을 이메일 |

무작위 문자열: `openssl rand -base64 36 | tr -d '/+='`

## 4. 인증서 → 실행

```bash
sh deploy/first-cert.sh                                   # 처음 한 번 (nginx를 켜기 전)
docker compose -f docker-compose.prod.yml up -d --build   # 첫 빌드는 몇 분 걸린다
docker compose -f docker-compose.prod.yml ps              # 5개 모두 Up, api는 healthy

# 사장님 로그인 계정 (비밀번호는 입력 창에)
docker compose -f docker-compose.prod.yml exec api python -m app.users add 이메일 --role owner
```

`https://<DOMAIN>`으로 들어가 로그인 → 홈 → 질문하기를 확인한다.

## 5. 업데이트

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build   # 바뀐 이미지만 다시 빌드한다
docker image prune -f                                     # 지난 이미지 정리
```

## 자주 쓰는 명령

```bash
P="docker compose -f docker-compose.prod.yml"
$P logs -f api            # 로그 (web, nginx, db, certbot도 같은 방식)
$P exec db psql -U songhak songhak
$P exec db pg_dump -U songhak songhak | gzip > ~/backup-$(date +%F).sql.gz   # 백업
```

## 아직 하지 않은 것

- 실데이터 가져오기와 하루 1회 자동 동기화 (4주차 11/11)
- 데모 DB와 데모 계정 (11/10)
- 자동 백업
- 보안 점검: 의존성, 비밀키 관리 (11/12)

## 로컬에서 미리 확인한 것 (2026-11-09)

자체 서명 인증서로 같은 compose를 Windows Docker에서 띄워 확인했다.
- http → https 이동, 도메인 없는 요청 차단
- 로그인 쿠키 `HttpOnly; Secure; SameSite=lax`, 보안 헤더 4개
- 비밀번호 틀림 401, 모든 화면 200
- 재시작 시 마이그레이션·계정 설정 재실행 문제없음
- 실행 중 메모리 합계 약 160MB

실수: Next.js 응답 헤더(Link 미리 불러오기)가 nginx 기본 버퍼보다 커서 화면만 502가 났다(API는 정상). `proxy_buffer_size`를 늘려 해결했다.

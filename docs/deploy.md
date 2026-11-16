# 배포 (AWS EC2)

```
                 ┌─ DOMAIN       ──▶ web      ──/api/*──▶ api      ──▶ db        운영 (실데이터)
인터넷 ─443─▶ nginx
                 └─ demo.DOMAIN  ──▶ web-demo ──/api/*──▶ api-demo ──▶ db-demo   공개 데모 (가짜 데이터)
          80 → 443으로 보냄          certbot: 12시간마다 인증서 갱신 확인
```

- 설정 파일: [docker-compose.prod.yml](../docker-compose.prod.yml), [deploy/nginx/templates/default.conf.template](../deploy/nginx/templates/default.conf.template)
- 바깥에 열리는 포트는 nginx의 80·443뿐이다. DB·API는 compose 내부 네트워크에서만 접속할 수 있다.
- api 컨테이너는 켤 때마다 `alembic upgrade head`와 질의용 읽기 전용 계정 설정을 실행한다. 둘 다 여러 번 실행해도 안전하다.
- 로그인 쿠키는 `Secure`(HTTPS 전용)로 고정된다. 그래서 HTTPS 없이는 로그인이 되지 않는다.
- 데모는 DB·DB 비밀번호·로그인 서명 키(`DEMO_AUTH_SECRET`)가 모두 운영과 따로다. 서명 키가 같으면 데모에서 받은 쿠키로 운영에 들어갈 수 있으므로 반드시 다른 값을 쓴다.
- 데모 보호: 하루 질문 20회(모든 방문자 합계, `DEMO_ASK_DAILY_LIMIT`), IP당 API 초당 5회(nginx).

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
| `DEMO_POSTGRES_PASSWORD`, `DEMO_QA_DB_PASSWORD`, `DEMO_AUTH_SECRET` | 데모용. 위 값과 **다른** 무작위 문자열 |
| `DOMAIN` | 무료 주소 `<IP의 점을 -로>.sslip.io` (예: IP 3.38.1.2 → `3-38-1-2.sslip.io`). 도메인을 사면 이 값만 바꾸고 인증서를 새로 받는다 |
| `CERTBOT_EMAIL` | 선택. Let's Encrypt 계정 이메일 (2025년부터 만료 알림 메일은 오지 않음) |

무작위 문자열: `openssl rand -base64 36 | tr -d '/+='`

## 4. 인증서 → 실행

```bash
sh deploy/first-cert.sh                                   # 처음 한 번 (nginx를 켜기 전). DOMAIN과 demo.DOMAIN
docker compose -f docker-compose.prod.yml up -d --build   # 첫 빌드는 몇 분 걸린다
docker compose -f docker-compose.prod.yml ps              # 5개 모두 Up, api는 healthy

# 사장님 로그인 계정 (비밀번호는 입력 창에)
docker compose -f docker-compose.prod.yml exec api python -m app.users add 이메일 --role owner
```

빌드 중 `Failed to fetch ... from Google Fonts`가 나오면 글꼴을 내려받다 네트워크가 끊긴 것이다. 같은 명령을 다시 실행한다.

`https://<DOMAIN>`으로 들어가 로그인 → 홈 → 질문하기를 확인한다.

데모 데이터 (7/1 ~ 오늘, 가상의 정산처 5곳·직원 2명·발주):

```bash
docker compose -f docker-compose.prod.yml exec api-demo python -m app.demo.seed
```

`https://demo.<DOMAIN>`에서 "데모로 둘러보기"로 들어간다. seed는 이름이 `_demo`로 끝나는 DB에서만 돌고(모든 데이터를 지우고 다시 만듦), 날짜를 늘려 다시 만들어도 지난 날짜의 숫자는 그대로다.

## 하루 1회 동기화 (실데이터)

정산 앱(RTDB)·발주 앱(Firestore)을 매일 새벽 5시(KST)에 가져온다. 데모 데이터도 같은 때 오늘까지 늘린다.

**서비스 계정 (읽기 전용)**: 두 Firebase 프로젝트에 `backoffice-sync` 서비스 계정을 두고 읽기 역할만 준다.

| 프로젝트 | 역할 | 키 파일 (서버 `secrets/`) |
|---|---|---|
| songhak-payment (정산) | Firebase Realtime Database 뷰어 | `songhak-payment-sync.json` |
| songhak-inventory (발주) | Cloud Datastore 뷰어 | `songhak-inventory-sync.json` |

```bash
mkdir -p secrets data
# 키 2개를 secrets/에, 과거 가격 이력(price_history.json)을 data/에 복사한 뒤
sudo chown -R 10001:10001 secrets data && sudo chmod 400 secrets/*.json   # api 컨테이너 사용자(uid 10001)만 읽게
docker compose -f docker-compose.prod.yml up -d api
sh deploy/daily.sh            # 한 번 직접 실행해 확인

# cron 등록 (서버 시간은 UTC → 20:00 UTC = 05:00 KST)
( crontab -l 2>/dev/null; echo "0 20 * * * $HOME/songhak-backoffice/deploy/daily.sh >> $HOME/daily.log 2>&1" ) | crontab -
```

- 결과는 `sync_runs`에 남고 홈 화면 "마지막 동기화"에 보인다. 원본은 `data/snapshots/`에 14일치.
- 실패 알림: `.env`에 `ALERT_URL`(예: ntfy 주제 주소)을 넣으면 실패할 때 그 주소로 보낸다.
- 키를 바꾸려면: Google Cloud 콘솔 → IAM → 서비스 계정 → `backoffice-sync` → 키.

## 5. 업데이트

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build   # 바뀐 이미지만 다시 빌드한다
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload   # 꼭 실행 (아래 설명)
docker image prune -f                                     # 지난 이미지 정리
docker builder prune -af                                  # 빌드 캐시 정리 (디스크 20GB를 넘지 않게)
df -h /                                                   # 디스크 사용량 확인 (80%가 넘으면 원인부터 확인)
```

nginx는 켜질 때 한 번만 `web`, `web-demo` 같은 컨테이너 이름을 내부 IP로 바꿔 기억한다. 업데이트로 컨테이너가 새로 만들어지면 IP가 바뀌는데 nginx는 옛 IP로 계속 보낸다. 2026-11-10에는 web과 web-demo의 IP가 서로 뒤바뀌어 데모 주소에 운영 로그인 화면이 나왔다. 화면만 바뀌었고 각 화면은 자기 API로만 연결되므로 데이터가 섞이지는 않았다. `nginx -s reload`로 이름을 다시 찾게 해서 고쳤다.

빌드 캐시는 다음 빌드를 빠르게 하려고 남기는 중간 파일이라 지워도 서비스와 데이터에는 영향이 없다. 지우면 다음 빌드만 처음처럼 몇 분 더 걸린다. 정리하지 않으면 업데이트할 때마다 몇 GB씩 쌓여 디스크가 차고, 디스크가 차면 DB 쓰기와 동기화가 멈춘다.

## 6. 백업과 복원

- **매일 백업:** `deploy/daily.sh`가 05:00 동기화 **직전에** `deploy/backup.sh`를 부른다. 운영 DB를 `pg_dump` 사용자 지정 형식(압축)으로 `~/backups/songhak-YYYY-MM-DD.dump`에 저장하고 14일치만 남긴다. 폴더는 700, 파일은 600 권한.
- **데모 DB는 백업하지 않는다.** 가상 데이터라 `app.demo.seed`로 언제든 다시 만든다.
- **복원 확인:** `sh deploy/restore-check.sh`는 지금 DB를 백업해 임시 DB(`restore_check`)에 복원한다. 그다음 운영 DB와 모든 테이블의 행 수, 월별 정산 합계, 월별 인건비 합계를 비교하고 임시 DB를 지운다. 하나라도 다르면 차이를 보여 주고 실패(종료 코드 1)한다. 05:00 동기화와 겹치지 않는 시간에, 한 달에 한 번 실행한다.

실제로 운영 DB를 백업으로 되돌릴 때 (데이터를 덮어쓰므로 먼저 지금 상태를 백업해 둔다):

```bash
P="docker compose -f docker-compose.prod.yml"
sh deploy/backup.sh                                      # 되돌리기 전 상태도 남긴다 (같은 날이면 덮어쓰므로 파일 이름을 바꿔 둘 것)
$P stop api                                              # 쓰는 쪽을 멈춘다
$P exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --exit-on-error' < ~/backups/songhak-2026-11-16.dump
$P start api
```

백업이 없어도 원본 앱(Firebase)에서 다시 가져올 수 있는 데이터가 대부분이다. 백업에만 있는 것은 로그인 계정, 동기화 기록, 원본 앱에서 이미 지워진 기록(발주 앱은 7일이 지나면 지운다)이다.

### 서버 밖 보관 (S3)

서버(EBS)를 통째로 잃어도 백업이 남도록, `backup.sh`는 같은 파일을 S3에도 올린다.

- 버킷 `songhak-backoffice-backup-<계정ID>` (서울). 퍼블릭 액세스는 모두 차단하고 SSE-S3로 암호화한다. 수명 주기 규칙 `expire-30d`가 `db/` 아래 객체를 30일 뒤 지운다.
- 인증: EC2 인스턴스 역할 `songhak-backoffice-backup`. 서버에 키 파일이 없다. 인라인 정책 `put-backup-only`는 `db/*`에 대한 `s3:PutObject`만 허용한다. 그래서 서버가 털려도 S3의 백업을 읽거나 지우거나 덮어 볼 수 없다. 2026-11-16에 목록 보기, 내려받기, 지우기, 다른 경로 올리기가 모두 AccessDenied인 것을 확인했다.
- 켜는 방법: 서버 `.env`에 `BACKUP_S3_URI=s3://버킷/db`. 비어 있으면 S3에 올리지 않는다.
- AWS CLI는 서버에 설치하지 않고 `amazon/aws-cli` 이미지로 실행한다. `--network host`는 컨테이너가 인스턴스 메타데이터(역할 자격 증명)에 닿게 하려고 쓴다.
- 파일 이름은 UTC 시각까지 넣어서(`songhak-2026-11-16T083012Z.dump`) 같은 날 여러 번 올려도 덮어쓰지 않는다.

S3 백업으로 복원할 때는 서버가 읽을 수 없으므로 사람이 내려받는다.
1. AWS 콘솔 → CloudShell에서 `aws s3 cp s3://버킷/db/파일 .`로 받는다. 또는 S3 콘솔에서 다운로드한다.
2. 받은 파일을 서버에 올린다(`scp`).
3. `sh deploy/restore-check.sh 파일`로 먼저 복원이 되는지 확인한 뒤, 위 절차로 되돌린다.

## 자주 쓰는 명령

```bash
P="docker compose -f docker-compose.prod.yml"
$P logs -f api            # 로그 (web, nginx, db, certbot도 같은 방식)
$P exec db psql -U songhak songhak
sh deploy/backup.sh       # 지금 바로 백업
sh deploy/restore-check.sh   # 백업이 복원되는지 확인
```

## 아직 하지 않은 것

- 앱 DB 계정 분리: 지금은 앱이 superuser로 접속한다 (마이그레이션 계정과 앱 계정을 나눌 예정)

## 로컬에서 미리 확인한 것 (2026-11-09)

자체 서명 인증서로 같은 compose를 Windows Docker에서 띄워 확인했다.
- http → https 이동, 도메인 없는 요청 차단
- 로그인 쿠키 `HttpOnly; Secure; SameSite=lax`, 보안 헤더 4개
- 비밀번호 틀림 401, 모든 화면 200
- 재시작 시 마이그레이션·계정 설정 재실행 문제없음
- 실행 중 메모리 합계 약 160MB

실수: Next.js 응답 헤더(Link 미리 불러오기)가 nginx 기본 버퍼보다 커서 화면만 502가 났다(API는 정상). `proxy_buffer_size`를 늘려 해결했다.

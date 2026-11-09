#!/bin/sh
# 매일 새벽 5시(KST)에 cron이 실행한다 (등록 방법은 docs/deploy.md).
# 1) 실데이터 동기화: Firebase → 운영 DB (실패하면 ALERT_URL로 알림)
# 2) 데모 데이터를 오늘까지 늘리기 (지난 날짜의 숫자는 그대로)
cd "$(dirname "$0")/.." || exit 1
C="docker compose -f docker-compose.prod.yml"
echo "== $(date -u +%FT%TZ) =="
$C exec -T api python -m app.etl.sync
sync_status=$?
$C exec -T api-demo python -m app.demo.seed
demo_status=$?
[ "$sync_status" -eq 0 ] && [ "$demo_status" -eq 0 ]

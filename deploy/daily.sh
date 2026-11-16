#!/bin/sh
# 매일 새벽 5시(KST)에 cron이 실행한다 (등록 방법은 docs/deploy.md).
# 0) 운영 DB 백업 (동기화로 데이터가 바뀌기 전에, 14일치 보관)
# 1) 실데이터 동기화: Firebase → 운영 DB (실패하면 ALERT_URL로 알림)
# 2) 지난달 정산서 PDF 보관: 아직 없을 때만 만든다 (보통 매달 1일, 실패하면 다음 날 다시)
# 3) 데모 데이터를 오늘까지 늘리기 (지난 날짜의 숫자는 그대로)
# 하나가 실패해도 나머지는 계속한다. 하나라도 실패하면 종료 코드가 0이 아니다.
cd "$(dirname "$0")/.." || exit 1
C="docker compose -f docker-compose.prod.yml"
echo "== $(date -u +%FT%TZ) =="
sh deploy/backup.sh
backup_status=$?
$C exec -T api python -m app.etl.sync
sync_status=$?
# 동기화 뒤에 만들어야 지난달 마지막 날 주문까지 들어간다
$C exec -T api python -m app.invoices
invoice_status=$?
# 데모 seed는 TRUNCATE를 써서 앱 계정으로는 안 된다. 관리 계정이 있는 migrate-demo로 한 번 돌린다
$C run --rm -T migrate-demo python -m app.demo.seed
demo_status=$?
[ "$backup_status" -eq 0 ] && [ "$sync_status" -eq 0 ] && [ "$invoice_status" -eq 0 ] && [ "$demo_status" -eq 0 ]

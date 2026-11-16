#!/bin/sh
# 백업이 실제로 복원되는지 확인한다.
# 지금 운영 DB를 백업 → 임시 DB(restore_check)에 복원 → 운영 DB와 비교 → 임시 DB 삭제.
# 비교: 모든 테이블의 행 수, 월별 정산 합계, 월별 인건비 합계. 다르면 실패(종료 코드 1).
# 05:00 동기화와 겹치지 않는 시간에 실행한다 (사이에 데이터가 바뀌면 비교가 어긋난다).
set -eu
cd "$(dirname "$0")/.."
C="${COMPOSE:-docker compose -f docker-compose.prod.yml}"   # 로컬 시험: COMPOSE="docker compose"
FILE="${1:-}"

if [ -z "$FILE" ]; then
  sh deploy/backup.sh
  FILE="$(ls -t "${BACKUP_DIR:-$HOME/backups}"/songhak-*.dump | head -1)"
fi
echo "복원할 파일: $FILE"

psql_db() { $C exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d $1 -v ON_ERROR_STOP=1 -AtX"; }

cleanup() { echo 'DROP DATABASE IF EXISTS restore_check;' | psql_db postgres >/dev/null; }
trap cleanup EXIT
cleanup
echo 'CREATE DATABASE restore_check;' | psql_db postgres >/dev/null

start=$(date +%s)
# 소유자·권한은 복원하지 않는다 (임시 DB라 필요 없고, 역할이 없어도 실패하지 않게)
$C exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d restore_check --no-owner --no-acl --exit-on-error' < "$FILE"
echo "복원 시간: $(( $(date +%s) - start ))초"

SUMMARY="
SELECT 'rows:' || table_name || '=' ||
  (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM public.%I', table_name), false, true, '')))[1]::text
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name;
SELECT 'sales:' || m || '=' || s FROM (
  SELECT to_char(order_date, 'YYYY-MM') AS m, sum(amount) AS s FROM qa.sales_items GROUP BY 1
) t ORDER BY m;
SELECT 'payroll:' || month || '=' || sum(total) FROM qa.payroll GROUP BY month ORDER BY month;
"
live="$(echo "$SUMMARY" | psql_db '"$POSTGRES_DB"')"
restored="$(echo "$SUMMARY" | psql_db restore_check)"

if [ "$live" = "$restored" ]; then
  echo "$restored" | sed 's/^/  /'
  echo "OK: 테이블 $(echo "$restored" | grep -c '^rows:')개 행 수, 월별 정산·인건비 합계가 운영 DB와 같다"
else
  echo "FAIL: 운영 DB와 복원본이 다르다 (- 운영, + 복원본)"
  a=$(mktemp); b=$(mktemp)
  echo "$live" > "$a"; echo "$restored" > "$b"
  diff -u "$a" "$b" || true
  rm -f "$a" "$b"
  exit 1
fi

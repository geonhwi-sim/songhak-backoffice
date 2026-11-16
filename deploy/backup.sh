#!/bin/sh
# 운영 DB를 백업한다. daily.sh가 동기화 직전에 부르고, 손으로 실행해도 된다.
# 결과: ~/backups/songhak-YYYY-MM-DD.dump (pg_dump 사용자 지정 형식, 압축됨). 14일치만 남긴다.
# 데모 DB는 가상 데이터라 언제든 다시 만들 수 있어서 백업하지 않는다.
set -eu
cd "$(dirname "$0")/.."
C="${COMPOSE:-docker compose -f docker-compose.prod.yml}"   # 로컬 시험: COMPOSE="docker compose"
DIR="${BACKUP_DIR:-$HOME/backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
mkdir -p "$DIR"
chmod 700 "$DIR"

out="$DIR/songhak-$(date +%F).dump"
tmp="$out.part"
# 끝까지 성공했을 때만 이름을 바꿔서, 중간에 끊긴 파일이 백업처럼 남지 않게 한다
$C exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$tmp"
mv "$tmp" "$out"
chmod 600 "$out"
echo "backup: $out ($(du -h "$out" | cut -f1))"

find "$DIR" -name 'songhak-*.dump' -mtime +"$KEEP_DAYS" -print -delete

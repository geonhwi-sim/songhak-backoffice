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

# 서버 밖 보관: .env에 BACKUP_S3_URI(예: s3://버킷/db)가 있으면 S3에도 올린다.
# 키 파일 없이 EC2 역할(올리기만 허용)로 인증하고, 30일이 지나면 S3 수명 주기 규칙이 지운다.
# AWS CLI는 설치하지 않고 공식 이미지로 실행한다 (--network host: 인스턴스 메타데이터에 닿게).
S3_URI="${BACKUP_S3_URI:-$(sed -n 's/^BACKUP_S3_URI=//p' .env 2>/dev/null | tail -1)}"
if [ -n "$S3_URI" ]; then
  key="songhak-$(date -u +%Y-%m-%dT%H%M%SZ).dump"
  docker run --rm --network host -v "$DIR:/backups:ro" -e AWS_REGION="${AWS_REGION:-ap-northeast-2}" \
    amazon/aws-cli:2.39.3 s3 cp --only-show-errors "/backups/$(basename "$out")" "$S3_URI/$key"
  echo "s3: $S3_URI/$key"
fi

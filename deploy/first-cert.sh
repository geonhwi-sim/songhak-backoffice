#!/bin/sh
# 처음 한 번만: Let's Encrypt 인증서를 받는다. nginx를 켜기 전에 저장소 루트에서 실행한다.
#   sh deploy/first-cert.sh
# 이후 갱신은 certbot 서비스가 알아서 한다.
set -eu

[ -f .env ] || { echo ".env가 없습니다. 저장소 루트에서 실행하세요."; exit 1; }
set -a; . ./.env; set +a
: "${DOMAIN:?.env에 DOMAIN을 설정하세요}"

# Let's Encrypt는 2025년부터 만료 알림 메일을 보내지 않는다. 이메일은 계정 복구용 선택 사항
if [ -n "${CERTBOT_EMAIL:-}" ]; then
  contact="--email $CERTBOT_EMAIL --no-eff-email"
else
  contact="--register-unsafely-without-email"
fi

# nginx가 아직 80번을 쓰지 않으므로 certbot이 직접 80번으로 확인 요청을 받는다
# shellcheck disable=SC2086
docker compose -f docker-compose.prod.yml run --rm -p 80:80 --entrypoint certbot certbot \
  certonly --standalone -d "$DOMAIN" -d "demo.$DOMAIN" $contact --agree-tos

echo "인증서 발급 완료: $DOMAIN, demo.$DOMAIN"

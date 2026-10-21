#!/usr/bin/env bash
# 首次申请 / 手动重新签发 Let's Encrypt 证书（HTTP-01 webroot）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DOMAIN_WWW="${CERTBOT_DOMAIN_WWW:-www.youjulab.com}"
DOMAIN_APEX="${CERTBOT_DOMAIN_APEX:-youjulab.com}"
# 根域名需在公网有 A/AAAA 记录。DNSPod 未配 apex 时保持 0。
INCLUDE_APEX="${CERTBOT_INCLUDE_APEX:-0}"
EMAIL="${CERTBOT_EMAIL:-}"
WEBROOT="${ROOT}/deploy/certbot-www"

mkdir -p "$WEBROOT" "${ROOT}/deploy/certs"

if ! command -v certbot >/dev/null 2>&1; then
  echo "未安装 certbot。Debian/Ubuntu: apt-get install -y certbot" >&2
  exit 1
fi

cd "$ROOT"
docker compose up -d web

email_args=()
if [[ -n "$EMAIL" ]]; then
  email_args=(--email "$EMAIL")
else
  email_args=(--register-unsafely-without-email)
fi

domain_args=(-d "$DOMAIN_WWW")
if [[ "$INCLUDE_APEX" == "1" ]]; then
  domain_args+=(-d "$DOMAIN_APEX")
fi

certbot certonly \
  --webroot \
  -w "$WEBROOT" \
  "${domain_args[@]}" \
  --agree-tos \
  --non-interactive \
  --expand \
  "${email_args[@]}" \
  --keep-until-expiring \
  --deploy-hook "${ROOT}/deploy/certbot/deploy-hook.sh"

YOUJULAB_ROOT="$ROOT" CERTBOT_DOMAIN="$DOMAIN_WWW" "${ROOT}/deploy/certbot/deploy-hook.sh"
echo "完成。浏览器打开 https://${DOMAIN_WWW}"

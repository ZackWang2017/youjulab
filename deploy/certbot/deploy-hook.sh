#!/usr/bin/env bash
# certbot renew --deploy-hook 调用：把证书拷到 Nginx 挂载目录并热加载。
set -euo pipefail

ROOT="${YOUJULAB_ROOT:-/apps/youjulab}"
DOMAIN="${CERTBOT_DOMAIN:-www.youjulab.com}"
LIVE="/etc/letsencrypt/live/${DOMAIN}"
DEST="${ROOT}/deploy/certs"

[[ -f "${LIVE}/fullchain.pem" && -f "${LIVE}/privkey.pem" ]] || {
  echo "缺少 ${LIVE}/fullchain.pem 或 privkey.pem" >&2
  exit 1
}

mkdir -p "$DEST"
cp -L "${LIVE}/fullchain.pem" "${DEST}/fullchain.pem"
cp -L "${LIVE}/privkey.pem" "${DEST}/privkey.pem"
chmod 644 "${DEST}/fullchain.pem"
chmod 600 "${DEST}/privkey.pem"

cd "$ROOT"
if docker compose ps --status running --services 2>/dev/null | grep -qx web; then
  docker compose exec -T web nginx -s reload
  echo "已更新证书并 reload web"
else
  echo "证书已写入 ${DEST}，web 未运行，下次启动会加载"
fi

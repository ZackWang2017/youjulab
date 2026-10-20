#!/bin/sh
# 正式证书放到 /etc/nginx/certs/fullchain.pem 与 privkey.pem。
# 没有证书时生成 www.youjulab.com 的临时自签证书，保证 HTTP 与 HTTPS 都能启动。
set -eu

cert=/etc/nginx/certs/fullchain.pem
key=/etc/nginx/certs/privkey.pem
mkdir -p /etc/nginx/certs

if [ ! -s "$cert" ] || [ ! -s "$key" ]; then
  echo "未挂载 TLS 证书，生成 www.youjulab.com 的临时自签证书" >&2
  openssl req -x509 -nodes -newkey rsa:2048 -days 30 \
    -keyout "$key" -out "$cert" \
    -subj "/CN=www.youjulab.com" \
    -addext "subjectAltName=DNS:www.youjulab.com"
fi

exec /docker-entrypoint.sh "$@"

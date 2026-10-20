#!/bin/sh
set -eu

cd /app/mcp-server

attempt=0
until python -m db.bootstrap; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "finresearch schema init failed" >&2
    exit 1
  fi
  echo "waiting for postgres before schema init ($attempt/30)" >&2
  sleep 2
done

exec python server.py

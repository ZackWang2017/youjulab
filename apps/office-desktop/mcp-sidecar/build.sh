#!/usr/bin/env bash
# 构建 mcp-server PyInstaller sidecar（onedir）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SIDECAR="$(cd "$(dirname "$0")" && pwd)"
MCP="$ROOT/mcp-server"
OUT="$SIDECAR/dist"
WORK="$SIDECAR/build"
PY="${YOUJU_PYTHON:-$ROOT/.venv/bin/python}"

if [[ ! -x "$PY" ]]; then
  echo "找不到 Python：$PY（可设 YOUJU_PYTHON）" >&2
  exit 1
fi

echo "== install build deps =="
"$PY" -m pip install -q -r "$MCP/requirements.txt" "mcp[cli]>=1.2,<2" "pyinstaller>=6.3"

echo "== pyinstaller =="
rm -rf "$OUT" "$WORK"
"$PY" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$OUT" \
  --workpath "$WORK" \
  "$SIDECAR/youju-mcp-server.spec"

BIN="$OUT/youju-mcp-server/youju-mcp-server"
if [[ "$(uname -s)" == "Darwin" || "$(uname -s)" == "Linux" ]]; then
  chmod +x "$BIN"
fi

echo "== smoke =="
FINANCE_MCP_TRANSPORT=http \
FINANCE_MCP_HOST=127.0.0.1 \
FINANCE_MCP_PORT=18766 \
OFFICE_API_URL=http://127.0.0.1:18765 \
MCP_ACCESS_TOKEN=desktop-sidecar-smoke-token-123456 \
ACCOUNT_JWT_SECRET=desktop-sidecar-smoke-jwt-secret-123456 \
"$BIN" >"$SIDECAR/.smoke.log" 2>&1 &
PID=$!
cleanup() {
  kill "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
}
trap cleanup EXIT

ok=0
for _ in $(seq 1 80); do
  if curl -fsS "http://127.0.0.1:18766/health" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 0.25
done
if [[ "$ok" != 1 ]]; then
  echo "mcp sidecar 健康检查失败，日志：" >&2
  tail -80 "$SIDECAR/.smoke.log" >&2 || true
  exit 1
fi
echo "mcp sidecar ok: $BIN"
du -sh "$OUT/youju-mcp-server"

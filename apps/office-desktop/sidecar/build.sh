#!/usr/bin/env bash
# 构建 office-backend PyInstaller sidecar（onedir）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SIDECAR="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/apps/office-backend"
OUT="$SIDECAR/dist"
WORK="$SIDECAR/build"
# 仓库 .venv 的解释器经 PyInstaller 冻结后 ssl.SSLContext() 会报 Errno 2（https 全部失败），
# 因此默认用 uv 管理的 CPython 单独建打包环境；YOUJU_PYTHON 可强制指定解释器。
BUILD_VENV="$SIDECAR/.build-venv"
if [[ -n "${YOUJU_PYTHON:-}" ]]; then
  PY="$YOUJU_PYTHON"
else
  PY="$BUILD_VENV/bin/python"
  if [[ ! -x "$PY" ]]; then
    if ! command -v uv >/dev/null 2>&1; then
      echo "需要 uv 来创建打包环境（https://docs.astral.sh/uv/），或设置 YOUJU_PYTHON。" >&2
      exit 1
    fi
    echo "== create build venv (uv python 3.12) =="
    uv venv --python 3.12 --seed "$BUILD_VENV"
  fi
fi

if [[ ! -x "$PY" ]]; then
  echo "找不到 Python：$PY（可设 YOUJU_PYTHON）" >&2
  exit 1
fi

echo "== install build deps =="
"$PY" -m pip install -q -r "$BACKEND/requirements.txt" "pyinstaller>=6.3"

echo "== pyinstaller =="
rm -rf "$OUT" "$WORK"
"$PY" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$OUT" \
  --workpath "$WORK" \
  "$SIDECAR/youju-office-backend.spec"

BIN="$OUT/youju-office-backend/youju-office-backend"
if [[ "$(uname -s)" == "Darwin" || "$(uname -s)" == "Linux" ]]; then
  chmod +x "$BIN"
fi

echo "== smoke =="
# 假账号服务：覆盖登录后 bind-account 对 /api/account/me 的出站请求（曾因 frozen urllib 失败）。
MOCK_PORT=18764
"$PY" - <<'PY' >"$SIDECAR/.smoke-mock.log" 2>&1 &
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") == "/api/account/me":
            body = b'{"id":"smoke-account","email":"smoke@example.com"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()
    def log_message(self, *_args):
        return
HTTPServer(("127.0.0.1", 18764), H).serve_forever()
PY
MOCK_PID=$!

OFFICE_STORE=sqlite \
OFFICE_SQLITE_PATH="$SIDECAR/.smoke-office.sqlite" \
OFFICE_VECTOR_BACKEND=sqlite \
OFFICE_VECTOR_SQLITE_PATH="$SIDECAR/.smoke-vectors.sqlite" \
OFFICE_FILE_ROOT="$SIDECAR/.smoke-files" \
OFFICE_API_HOST=127.0.0.1 \
OFFICE_API_PORT=18765 \
OFFICE_DESKTOP=1 \
YOUJU_DEBUG_SSL=1 \
MCP_REMOTE_BASE_URL="http://127.0.0.1:$MOCK_PORT" \
MCP_LOCAL_BASE_URL="http://127.0.0.1:$MOCK_PORT" \
"$BIN" >"$SIDECAR/.smoke.log" 2>&1 &
PID=$!
cleanup() {
  kill "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
  kill "$MOCK_PID" 2>/dev/null || true
  wait "$MOCK_PID" 2>/dev/null || true
  rm -f "$SIDECAR/.smoke-office.sqlite" "$SIDECAR/.smoke-office.sqlite-wal" "$SIDECAR/.smoke-office.sqlite-shm"
  rm -f "$SIDECAR/.smoke-vectors.sqlite"
  rm -rf "$SIDECAR/.smoke-files"
}
trap cleanup EXIT

ok=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:18765/health" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 0.25
done
if [[ "$ok" != 1 ]]; then
  echo "sidecar 健康检查失败，日志：" >&2
  tail -80 "$SIDECAR/.smoke.log" >&2 || true
  exit 1
fi
if ! grep -q '\[youju\] SSLContext ok' "$SIDECAR/.smoke.log"; then
  echo "sidecar SSL 自检失败（https 模型接口将不可用），日志：" >&2
  grep '\[youju\]' "$SIDECAR/.smoke.log" >&2 || tail -40 "$SIDECAR/.smoke.log" >&2 || true
  exit 1
fi
BIND="$(curl -sS -X POST "http://127.0.0.1:18765/local/bind-account" -H "X-Remote-Token: smoke-token")"
if ! printf '%s' "$BIND" | grep -q '"email":"smoke@example.com"'; then
  echo "sidecar bind-account 失败：$BIND" >&2
  tail -80 "$SIDECAR/.smoke.log" >&2 || true
  exit 1
fi
echo "sidecar ok: $BIN"
du -sh "$OUT/youju-office-backend"

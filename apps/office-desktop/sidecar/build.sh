#!/usr/bin/env bash
# 构建 office-backend PyInstaller sidecar（onedir）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SIDECAR="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/apps/office-backend"
OUT="$SIDECAR/dist"
WORK="$SIDECAR/build"
PY="${YOUJU_PYTHON:-$ROOT/.venv/bin/python}"

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
OFFICE_STORE=sqlite \
OFFICE_SQLITE_PATH="$SIDECAR/.smoke-office.sqlite" \
OFFICE_VECTOR_BACKEND=sqlite \
OFFICE_VECTOR_SQLITE_PATH="$SIDECAR/.smoke-vectors.sqlite" \
OFFICE_FILE_ROOT="$SIDECAR/.smoke-files" \
OFFICE_API_HOST=127.0.0.1 \
OFFICE_API_PORT=18765 \
OFFICE_DESKTOP=1 \
"$BIN" >"$SIDECAR/.smoke.log" 2>&1 &
PID=$!
cleanup() {
  kill "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
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
echo "sidecar ok: $BIN"
du -sh "$OUT/youju-office-backend"

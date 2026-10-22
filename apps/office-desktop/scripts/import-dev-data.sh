#!/usr/bin/env bash
# 把仓库开发态 data/ 导入到打包桌面 app 的 userData。
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SRC_ROOT="${REPO_ROOT}/data"
DST="${HOME}/Library/Application Support/youjulab-desktop"
SRC_DB="${SRC_ROOT}/office-local/office.sqlite"
SRC_VEC="${SRC_ROOT}/office-local/office-vectors.sqlite"
SRC_FINANCE="${SRC_ROOT}/finance-local/finresearch.sqlite"
SRC_FILES="${SRC_ROOT}/office-files"

if [[ ! -f "$SRC_DB" ]]; then
  echo "找不到开发库: $SRC_DB" >&2
  exit 1
fi

if pgrep -f 'youjulab.app/Contents/MacOS/youjulab' >/dev/null 2>&1; then
  echo "检测到桌面 app 正在运行，先退出…"
  pkill -f 'youjulab.app/Contents/MacOS/youjulab' || true
  sleep 2
fi

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="${DST}/backup-before-import-${TS}"
mkdir -p "$BACKUP"
for f in office.sqlite office.sqlite-wal office.sqlite-shm office-vectors.sqlite finresearch.sqlite finresearch.sqlite-wal finresearch.sqlite-shm; do
  [[ -e "${DST}/${f}" ]] && mv "${DST}/${f}" "$BACKUP/" || true
done
if [[ -d "${DST}/office-files" ]] && [[ -n "$(ls -A "${DST}/office-files" 2>/dev/null || true)" ]]; then
  mv "${DST}/office-files" "${BACKUP}/office-files"
fi
mkdir -p "${DST}/office-files"

sqlite3 "$SRC_DB" "PRAGMA wal_checkpoint(PASSIVE);" >/dev/null || true
sqlite3 "$SRC_DB" ".backup '${DST}/office.sqlite'"
[[ -f "$SRC_VEC" ]] && cp -f "$SRC_VEC" "${DST}/office-vectors.sqlite"
if [[ -f "$SRC_FINANCE" ]]; then
  sqlite3 "$SRC_FINANCE" "PRAGMA wal_checkpoint(PASSIVE);" >/dev/null || true
  sqlite3 "$SRC_FINANCE" ".backup '${DST}/finresearch.sqlite'"
fi
rsync -a --delete "${SRC_FILES}/" "${DST}/office-files/"

python3 - "$DST" "$SRC_FILES" <<'PY'
import json, sys
from pathlib import Path
dst = Path(sys.argv[1])
source_files = Path(sys.argv[2])
cfg = dst / "app-config.json"
data = {}
if cfg.exists():
    try:
        old = json.loads(cfg.read_text())
        if isinstance(old, dict):
            data = old
    except Exception:
        pass
data.setdefault("remoteBaseUrl", "http://127.0.0.1:8000")
data["dataMode"] = "userData"
if not data.get("pdfDir") or Path(str(data["pdfDir"])).expanduser() == source_files:
    data["pdfDir"] = str(dst / "office-files")
cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
print("imported ->", dst)
print("office.sqlite", (dst / "office.sqlite").stat().st_size, "bytes")
if (dst / "finresearch.sqlite").exists():
    print("finresearch.sqlite", (dst / "finresearch.sqlite").stat().st_size, "bytes")
print("office-files", sum(1 for p in (dst / "office-files").rglob("*") if p.is_file()), "files")
PY

echo "完成。重新打开 youjulab.app 即可。"

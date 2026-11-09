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

SRC_DB_REAL="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$SRC_DB")"
DST_DB_REAL="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${DST}/office.sqlite")"
if [[ "$SRC_DB_REAL" == "$DST_DB_REAL" ]]; then
  echo "源库与 userData 已是同一文件（可能是符号链接），无需导入: $SRC_DB_REAL"
  exit 0
fi

if pgrep -f 'youjulab.app/Contents/MacOS/youjulab' >/dev/null 2>&1; then
  echo "请先退出桌面 app 和本地 office-backend，再执行导入。" >&2
  exit 1
fi
if command -v lsof >/dev/null 2>&1; then
  for f in "$SRC_DB" "$SRC_VEC" "${DST}/office.sqlite" "${DST}/office-vectors.sqlite"; do
    if [[ -f "$f" ]] && lsof -t "$f" >/dev/null 2>&1; then
      echo "数据库仍被占用，请先停止对应服务: $f" >&2
      exit 1
    fi
  done
fi

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="${DST}/backup-before-import-${TS}"
mkdir -p "$BACKUP"
for f in office.sqlite office.sqlite-wal office.sqlite-shm office-vectors.sqlite office-vectors.sqlite-wal office-vectors.sqlite-shm finresearch.sqlite finresearch.sqlite-wal finresearch.sqlite-shm; do
  [[ -e "${DST}/${f}" ]] && mv "${DST}/${f}" "$BACKUP/" || true
done
if [[ -d "${DST}/office-files" ]] && [[ -n "$(ls -A "${DST}/office-files" 2>/dev/null || true)" ]]; then
  mv "${DST}/office-files" "${BACKUP}/office-files"
fi
mkdir -p "${DST}/office-files"

sqlite3 "$SRC_DB" "PRAGMA wal_checkpoint(PASSIVE);" >/dev/null || true
sqlite3 "$SRC_DB" ".backup '${DST}/office.sqlite'"
if [[ -f "$SRC_VEC" ]]; then
  # Snapshot includes committed WAL pages; copying only the main file can corrupt vec0.
  sqlite3 "$SRC_VEC" ".backup '${DST}/office-vectors.sqlite'"
fi
if [[ -f "$SRC_FINANCE" ]]; then
  sqlite3 "$SRC_FINANCE" "PRAGMA wal_checkpoint(PASSIVE);" >/dev/null || true
  sqlite3 "$SRC_FINANCE" ".backup '${DST}/finresearch.sqlite'"
fi
SRC_FILES_REAL="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$SRC_FILES")"
DST_FILES_REAL="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${DST}/office-files")"
if [[ "$SRC_FILES_REAL" != "$DST_FILES_REAL" ]]; then
  rsync -a --delete "${SRC_FILES}/" "${DST}/office-files/"
fi

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
data.setdefault("remoteBaseUrl", "http://192.168.0.5:8000")
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

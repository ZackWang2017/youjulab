#!/usr/bin/env bash
# 备份 Invp 数据库（MongoDB + Postgres），不含本地 PDF 文件。
# 用法（在仓库根目录）：
#   ./backup/backup.sh
#   ./backup/backup.sh --keep 7          # 只保留最近 7 份 .tar.gz
#   ./backup/backup.sh --out /path/to/dir # 自定义输出根目录
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_ROOT="${ROOT}/backup/archives"
KEEP=""
OUT_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) KEEP="${2:-}"; shift 2 ;;
    --out) OUT_OVERRIDE="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

[[ -n "$OUT_OVERRIDE" ]] && BACKUP_ROOT="$OUT_OVERRIDE"

load_env() {
  local env_file="$ROOT/.env"
  if [[ ! -f "$env_file" ]]; then
    echo "未找到 $env_file，请先配置环境变量。" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
}

have_cmd() { command -v "$1" >/dev/null 2>&1; }

stamp="$(date +%Y%m%d-%H%M%S)"
run_dir="${BACKUP_ROOT}/${stamp}"
mkdir -p "$run_dir"
load_env

MONGO_URL="${OFFICE_MONGO_URL:-mongodb://127.0.0.1:27017}"
MONGO_DB="${OFFICE_MONGO_DB:-office}"
OFFICE_PG="${OFFICE_PG_URL:-}"
FINANCE_PG="${FINANCE_DATABASE_URL:-}"

status_mongo="skipped"
status_office_pg="skipped"
status_finance_pg="skipped"
notes=()

echo "==> 备份目录: $run_dir"
echo "==> 不包含 OFFICE_FILE_ROOT PDF 文件（新机需重新上传或另拷贝）"

if [[ "$MONGO_URL" == memory://* ]]; then
  notes+=("OFFICE_MONGO_URL 为 memory://，已跳过 Mongo")
  echo "!! 跳过 Mongo（memory://）"
elif ! have_cmd mongodump; then
  notes+=("未安装 mongodump，已跳过 Mongo")
  echo "!! 跳过 Mongo（无 mongodump）。macOS: brew install mongodb/brew/mongodb-database-tools" >&2
else
  mongo_dir="${run_dir}/mongodb"
  mkdir -p "$mongo_dir"
  echo "==> mongodump ${MONGO_DB}"
  mongodump --uri="$MONGO_URL" --db="$MONGO_DB" --out="$mongo_dir"
  status_mongo="ok"
fi

dump_pg() {
  local url="$1" name="$2" outfile="$3"
  [[ -z "$url" ]] && return 1
  if ! have_cmd pg_dump; then
    notes+=("未安装 pg_dump，已跳过 ${name}")
    echo "!! 跳过 ${name}（无 pg_dump）" >&2
    return 1
  fi
  echo "==> pg_dump ${name}"
  pg_dump --dbname="$url" --format=custom --no-owner --no-acl --file="$outfile"
}

if dump_pg "$OFFICE_PG" "OFFICE_PG_URL" "${run_dir}/office_pg.dump"; then
  status_office_pg="ok"
elif [[ -z "$OFFICE_PG" ]]; then
  notes+=("未配置 OFFICE_PG_URL，已跳过 office Postgres")
fi

if dump_pg "$FINANCE_PG" "FINANCE_DATABASE_URL" "${run_dir}/finance_pg.dump"; then
  status_finance_pg="ok"
elif [[ -z "$FINANCE_PG" ]]; then
  notes+=("未配置 FINANCE_DATABASE_URL，已跳过 finance Postgres")
fi

cat > "${run_dir}/RESTORE.txt" <<EOF
Invp 数据库备份
时间: $(date -Iseconds 2>/dev/null || date)
主机: $(hostname 2>/dev/null || echo unknown)

已备份:
  MongoDB:     ${status_mongo}  (库名 ${MONGO_DB})
  OFFICE_PG:   ${status_office_pg}
  FINANCE_PG:  ${status_finance_pg}

未备份:
  - 本地 PDF（OFFICE_FILE_ROOT，默认 data/office-files）
  - .env 密钥（请单独安全转移）

恢复:
  1. 新机安装 MongoDB / Postgres（office 库建议启用 pgvector）
  2. 配置新机仓库根目录 .env
  3. ./backup/restore.sh ${run_dir}
     或 ./backup/restore.sh ${run_dir}.tar.gz
EOF

NOTES_JSON="$(printf '%s\n' "${notes[@]:-}" | python3 -c 'import json,sys; print(json.dumps([l for l in sys.stdin.read().splitlines() if l], ensure_ascii=False))')"
python3 - "$run_dir" "$status_mongo" "$MONGO_DB" "$status_office_pg" "$status_finance_pg" "$NOTES_JSON" <<'PY'
import json, os, sys
from datetime import datetime, timezone
run_dir, mongo, mongo_db, office_pg, finance_pg, notes_json = sys.argv[1:7]
data = {
    "created_at": datetime.now(timezone.utc).isoformat(),
    "mongo": mongo,
    "mongo_db": mongo_db,
    "office_pg": office_pg,
    "finance_pg": finance_pg,
    "includes_pdf_files": False,
    "notes": json.loads(notes_json),
    "files": sorted(os.listdir(run_dir)),
}
with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

archive="${run_dir}.tar.gz"
echo "==> 打包 ${archive}"
tar -C "$(dirname "$run_dir")" -czf "$archive" "$(basename "$run_dir")"

if [[ -n "$KEEP" ]]; then
  if [[ ! "$KEEP" =~ ^[0-9]+$ ]] || [[ "$KEEP" -lt 1 ]]; then
    echo "--keep 需要正整数" >&2
    exit 1
  fi
  echo "==> 仅保留最近 ${KEEP} 份 .tar.gz"
  # shellcheck disable=SC2012
  ls -1t "${BACKUP_ROOT}"/*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
    rm -f "$old"
    dir="${old%.tar.gz}"
    [[ -d "$dir" ]] && rm -rf "$dir"
  done
fi

echo
echo "完成。"
echo "  目录: $run_dir"
echo "  压缩: $archive"
echo "  Mongo=${status_mongo}  OFFICE_PG=${status_office_pg}  FINANCE_PG=${status_finance_pg}"

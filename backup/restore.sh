#!/usr/bin/env bash
# 从 backup/backup.sh 产出的目录或 .tar.gz 恢复数据库到当前 .env 指向的实例。
# 用法（在仓库根目录）：
#   ./backup/restore.sh backup/archives/20260320-120000
#   ./backup/restore.sh backup/archives/20260320-120000.tar.gz
#   ./backup/restore.sh ... --yes          # 跳过确认
# 注意：会覆盖目标库同名数据；不恢复本地 PDF 文件。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC=""
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) ASSUME_YES=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *)
      if [[ -z "$SRC" ]]; then SRC="$1"; shift
      else echo "未知参数: $1" >&2; exit 1
      fi
      ;;
  esac
done

if [[ -z "$SRC" ]]; then
  echo "请指定备份目录或 .tar.gz 路径。" >&2
  echo "示例: ./backup/restore.sh backup/archives/YYYYMMDD-HHMMSS.tar.gz" >&2
  exit 1
fi

# 相对路径相对仓库根
if [[ "$SRC" != /* ]]; then
  SRC="${ROOT}/${SRC}"
fi

load_env() {
  local env_file="$ROOT/.env"
  if [[ ! -f "$env_file" ]]; then
    echo "未找到 $env_file" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
}

have_cmd() { command -v "$1" >/dev/null 2>&1; }

workdir=""
cleanup() {
  if [[ -n "${workdir}" && -d "${workdir}" && "${workdir}" == /tmp/invp-restore-* ]]; then
    rm -rf "$workdir"
  fi
}
trap cleanup EXIT

if [[ -f "$SRC" && "$SRC" == *.tar.gz ]]; then
  workdir="$(mktemp -d /tmp/invp-restore-XXXXXX)"
  echo "==> 解压 $SRC"
  tar -xzf "$SRC" -C "$workdir"
  # 压缩包内应只有一层时间戳目录
  tops=()
  while IFS= read -r line; do
    tops+=("$line")
  done < <(find "$workdir" -mindepth 1 -maxdepth 1 -type d)
  if [[ ${#tops[@]} -ne 1 ]]; then
    echo "压缩包结构异常，期望单个备份目录。" >&2
    exit 1
  fi
  run_dir="${tops[0]}"
elif [[ -d "$SRC" ]]; then
  run_dir="$SRC"
else
  echo "路径不存在: $SRC" >&2
  exit 1
fi

if [[ ! -f "${run_dir}/manifest.json" && ! -f "${run_dir}/RESTORE.txt" ]]; then
  echo "不像有效备份目录: $run_dir" >&2
  exit 1
fi

load_env
MONGO_URL="${OFFICE_MONGO_URL:-mongodb://127.0.0.1:27017}"
MONGO_DB="${OFFICE_MONGO_DB:-office}"
OFFICE_PG="${OFFICE_PG_URL:-}"
FINANCE_PG="${FINANCE_DATABASE_URL:-}"

echo "==> 恢复来源: $run_dir"
echo "==> 目标 Mongo: ${MONGO_DB} @ ${MONGO_URL%%@*}…"
echo "==> 目标 OFFICE_PG: ${OFFICE_PG:+已配置}${OFFICE_PG:-未配置}"
echo "==> 目标 FINANCE_PG: ${FINANCE_PG:+已配置}${FINANCE_PG:-未配置}"
echo "==> PDF 文件不会恢复"
echo

if [[ "$ASSUME_YES" -ne 1 ]]; then
  read -r -p "将覆盖目标库数据，确认继续？[y/N] " ans
  case "$ans" in
    y|Y|yes|YES) ;;
    *) echo "已取消"; exit 0 ;;
  esac
fi

# --- Mongo ---
mongo_dump_dir=""
if [[ -d "${run_dir}/mongodb/${MONGO_DB}" ]]; then
  mongo_dump_dir="${run_dir}/mongodb/${MONGO_DB}"
elif [[ -d "${run_dir}/mongodb" ]]; then
  # mongodump 可能把库放在 mongodb/<dbname>
  first="$(find "${run_dir}/mongodb" -mindepth 1 -maxdepth 1 -type d | head -1 || true)"
  mongo_dump_dir="$first"
fi

if [[ -n "$mongo_dump_dir" && -d "$mongo_dump_dir" ]]; then
  if [[ "$MONGO_URL" == memory://* ]]; then
    echo "!! 目标为 memory://，跳过 Mongo 恢复" >&2
  elif ! have_cmd mongorestore; then
    echo "!! 缺少 mongorestore，跳过 Mongo" >&2
  else
    echo "==> mongorestore → ${MONGO_DB}"
    mongorestore --uri="$MONGO_URL" --db="$MONGO_DB" --drop "$mongo_dump_dir"
  fi
else
  echo "!! 备份中无 Mongo 数据，跳过"
fi

# --- Postgres ---
restore_pg() {
  local url="$1" dump="$2" label="$3"
  if [[ ! -f "$dump" ]]; then
    echo "!! 无 ${label} dump，跳过"
    return 0
  fi
  if [[ -z "$url" ]]; then
    echo "!! 未配置 ${label}，跳过 ${dump}" >&2
    return 0
  fi
  if ! have_cmd pg_restore; then
    echo "!! 缺少 pg_restore，跳过 ${label}" >&2
    return 0
  fi
  echo "==> pg_restore ${label}"
  # --clean --if-exists：先删再建；失败不因「对象不存在」中断
  pg_restore --dbname="$url" --clean --if-exists --no-owner --no-acl "$dump" \
    || echo "!! pg_restore ${label} 有警告（常见于扩展/已存在对象），请人工检查" >&2
}

restore_pg "$OFFICE_PG" "${run_dir}/office_pg.dump" "OFFICE_PG_URL"
restore_pg "$FINANCE_PG" "${run_dir}/finance_pg.dump" "FINANCE_DATABASE_URL"

echo
echo "恢复完成。"
echo "下一步建议："
echo "  1. 确认新机 .env 中模型密钥、MCP_ACCESS_TOKEN、JWT 等已配置"
echo "  2. 启动 office-api / mcp-server"
echo "  3. 需要 PDF 正文时：重新上传，或拷贝原机 OFFICE_FILE_ROOT 后再重建索引"
if [[ -f "${run_dir}/RESTORE.txt" ]]; then
  echo "  详见: ${run_dir}/RESTORE.txt"
fi

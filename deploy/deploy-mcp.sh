#!/usr/bin/env bash
# 仅在 mcp / 反代 / compose / postgres 初始化相关文件变更时重建 mcp-server 并重启栈。
# 用法（在仓库根目录）：
#   ./deploy/deploy-mcp.sh
#   ./deploy/deploy-mcp.sh --force
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG="${DEPLOY_MCP_LOG:-$ROOT/deploy/deploy-mcp.log}"
BRANCH="${DEPLOY_BRANCH:-main}"
REMOTE="${DEPLOY_REMOTE:-origin}"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
echo "==== $(date -Iseconds) deploy-mcp start ===="

have() { command -v "$1" >/dev/null 2>&1; }
have git || { echo "需要 git"; exit 1; }
have docker || { echo "需要 docker"; exit 1; }

OLD_SHA="$(git rev-parse HEAD)"
git fetch "$REMOTE" "$BRANCH"
git reset --hard "$REMOTE/$BRANCH"
NEW_SHA="$(git rev-parse HEAD)"
echo "HEAD $OLD_SHA -> $NEW_SHA"

PATH_RE='^(mcp-server/|docker-compose\.yml|deploy/Dockerfile\.nginx|deploy/nginx/mcp\.conf\.template|deploy/entrypoint-mcp\.sh|deploy/postgres/)'

need_build=0
if [[ "$FORCE" -eq 1 ]]; then
  need_build=1
  echo "force rebuild"
elif [[ "$OLD_SHA" == "$NEW_SHA" ]]; then
  echo "无新提交，跳过"
  echo "==== $(date -Iseconds) deploy-mcp done (noop) ===="
  exit 0
elif git diff --name-only "$OLD_SHA" "$NEW_SHA" | grep -qE "$PATH_RE"; then
  need_build=1
  echo "检测到 mcp 相关变更"
  git diff --name-only "$OLD_SHA" "$NEW_SHA" | grep -E "$PATH_RE" || true
else
  echo "变更与 mcp 部署无关，跳过编译"
  echo "==== $(date -Iseconds) deploy-mcp done (skip) ===="
  exit 0
fi

if [[ "$need_build" -eq 1 ]]; then
  docker compose build mcp-server nginx
fi
docker compose up -d
echo "==== $(date -Iseconds) deploy-mcp done ===="
docker compose ps

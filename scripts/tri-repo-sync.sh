#!/usr/bin/env bash
# 三仓同步辅助脚本。日常也可对 Cursor 说口令「三仓同步」。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PRIVATE_ROOT="$ROOT/private"
MODE="${1:-all}" # all | oss | private | status

die() { echo "error: $*" >&2; exit 1; }

assert_clean_secrets() {
  local staged
  staged="$(git -C "$ROOT" status --short)"
  echo "$staged" | grep -E '(^| )\.env$|\.env\.[^ ]|credentials\.json|weread-session' \
    && die "主仓出现疑似私密文件，已中止。请检查 git status。"
  echo "$staged" | grep -E '(^|/)private/' \
    && die "主仓试图跟踪 private/，已中止。private 应使用独立仓库。"
  return 0
}

status_all() {
  echo "== 主仓 ($ROOT) =="
  git -C "$ROOT" remote -v
  git -C "$ROOT" status -sb
  echo
  if [[ -d "$PRIVATE_ROOT/.git" ]]; then
    echo "== 私有仓 ($PRIVATE_ROOT) =="
    git -C "$PRIVATE_ROOT" remote -v
    git -C "$PRIVATE_ROOT" status -sb
  else
    echo "== 私有仓未初始化 =="
  fi
}

push_oss() {
  assert_clean_secrets || true
  local branch
  branch="$(git -C "$ROOT" branch --show-current)"
  [[ -n "$branch" ]] || die "主仓无当前分支"
  echo ">> push gitee ($branch)"
  git -C "$ROOT" push gitee "$branch"
  echo ">> push github ($branch)"
  git -C "$ROOT" push github "$branch"
}

push_private() {
  [[ -d "$PRIVATE_ROOT/.git" ]] || die "private/ 尚不是独立 Git 仓库"
  local branch
  branch="$(git -C "$PRIVATE_ROOT" branch --show-current)"
  [[ -n "$branch" ]] || die "私有仓无当前分支"
  echo ">> push private origin ($branch)"
  git -C "$PRIVATE_ROOT" push -u origin "$branch"
}

case "$MODE" in
  status) status_all ;;
  oss) push_oss ;;
  private) push_private ;;
  all)
    push_oss
    push_private
    ;;
  *)
    die "用法: $0 [all|oss|private|status]"
    ;;
esac

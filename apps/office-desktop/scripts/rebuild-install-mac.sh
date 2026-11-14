#!/usr/bin/env bash
set -euo pipefail

DESKTOP="$(cd "$(dirname "$0")/.." && pwd)"
TARGET='/Applications/youjulab.app'
STAGE=''

fail() { printf '\n错误：%s\n' "$*" >&2; exit 1; }
running() {
  /bin/ps -axo command= | /usr/bin/awk -v prefix="$1/Contents/" '
    index($0, prefix) == 1 { found=1 } END { exit !found }'
}
cleanup() {
  local result=$?
  if [[ -n "$STAGE" && -d "$STAGE" ]]; then /bin/rm -rf "$STAGE"; fi
  exit "$result"
}
trap cleanup EXIT

[[ "$(uname -s)" == Darwin ]] || fail '此脚本仅支持 macOS。'
[[ $# -le 1 && ( $# == 0 || "$1" == '--yes' ) ]] || fail '用法：bash scripts/rebuild-install-mac.sh [--yes]'
if [[ "${1:-}" != '--yes' ]]; then
  [[ -t 0 ]] || fail '请在终端运行；确认已保存内容后可使用 --yes。'
  printf '请先保存所有编辑内容。继续将退出 App、完整构建、直接替换旧版并启动（不备份）。继续？[y/N] '
  read -r answer
  [[ "$answer" == y || "$answer" == Y ]] || exit 0
fi

# Defaults are explicitly for local development; exported production URLs take precedence.
export YOUJU_RELEASE_BASE_URL="${YOUJU_RELEASE_BASE_URL:-http://192.168.0.5:8200}"
export YOUJU_ADMIN_BASE_URL="${YOUJU_ADMIN_BASE_URL:-$YOUJU_RELEASE_BASE_URL}"
ARCH="$(uname -m)"
case "$ARCH" in arm64) OUTPUT='mac-arm64';; x86_64) OUTPUT='mac';; *) fail "不支持的架构：$ARCH";; esac
SOURCE="$DESKTOP/release/$OUTPUT/youjulab.app"

printf '\n[1/5] 退出旧 App 及其本地后台…\n'
for app in "$TARGET" "$SOURCE"; do
  if running "$app"; then
    [[ -x "$app/Contents/MacOS/youjulab" ]] || fail "无法请求退出：$app"
    for ((attempt=1; attempt<=2; attempt++)); do
      "$app/Contents/MacOS/youjulab" --quit >/dev/null 2>&1 &
      helper=$!
      wait "$helper" || true
      for ((i=0; i<60; i++)); do
        if ! running "$app"; then break; fi
        sleep 1
      done
      if ! running "$app"; then break; fi
      if (( attempt == 1 )); then printf '退出尚未完成，再尝试一次正常退出…\n'; fi
    done
    if running "$app"; then fail 'App 或后台仍在运行，请手动退出后重试，不会强制终止或覆盖。'; fi
  fi
done

printf '\n[2/5] 完整构建 Web、Python sidecar 和 App（不生成 DMG）…\n'
cd "$DESKTOP"
npm run build:desktop
[[ -x "$SOURCE/Contents/MacOS/youjulab" ]] || fail "未找到构建产物：$SOURCE"
SIDECAR='Contents/Resources/sidecar/youju-office-backend/youju-office-backend'
[[ -x "$SOURCE/$SIDECAR" ]] || fail '构建产物缺少 Python 后台。'
/usr/bin/cmp -s "$DESKTOP/sidecar/dist/youju-office-backend/youju-office-backend" "$SOURCE/$SIDECAR" || fail 'App 中的后台与新构建产物不一致。'
[[ -w /Applications ]] || fail '当前用户不能写入 /Applications；未替换旧版，请检查权限。'
for app in "$TARGET" "$SOURCE"; do
  if running "$app"; then fail '构建期间 App 又被打开，请退出后重试。'; fi
done

printf '\n[3/5] 暂存并校验新 App…\n'
STAGE="$(/usr/bin/mktemp -d /Applications/.youjulab-install.XXXXXX)"
/usr/bin/ditto "$SOURCE" "$STAGE/youjulab.app"
/usr/bin/diff -qr "$SOURCE" "$STAGE/youjulab.app" >/dev/null || fail '复制校验失败，旧 App 未替换。'

printf '\n[4/5] 直接替换安装目录（不备份）…\n'
for app in "$TARGET" "$SOURCE"; do
  if running "$app"; then fail '复制期间 App 又被打开，已取消替换。'; fi
done
if [[ -e "$TARGET" ]]; then
  /bin/rm -rf "$TARGET"
fi
/bin/mv "$STAGE/youjulab.app" "$TARGET"

printf '\n[5/5] 启动新 App…\n'
/usr/bin/open "$TARGET"
printf '\n已安装并请求启动：%s\n' "$TARGET"
printf '用户数据目录未修改；旧 App 已直接替换，未创建备份。\n'

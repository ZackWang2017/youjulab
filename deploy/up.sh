#!/usr/bin/env bash
# 检查并安装数据库镜像，构建应用镜像（含 Python / Node 依赖），然后启动 Compose。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

WITH_EMBEDDING=0
PULL=0

usage() {
  cat <<'EOF'
用法: ./deploy/up.sh [--pull] [--with-embedding]

  --pull             即使本地已有数据库镜像也重新拉取
  --with-embedding   同时启动 Ollama，并把向量服务地址指向 http://ollama:11434
EOF
}

for arg in "$@"; do
  case "$arg" in
    --pull) PULL=1 ;;
    --with-embedding) WITH_EMBEDDING=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 docker。请先安装 Docker Desktop 或 Docker Engine。" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker 未在运行。请先启动 Docker。" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "未找到 Docker Compose v2。请安装带 compose 子命令的 Docker。" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已根据 .env.example 生成 .env。模型密钥仍为空，填写后执行: docker compose up -d office-backend"
fi

db_images=(
  "pgvector/pgvector:pg16"
  "mongo:7"
)
for image in "${db_images[@]}"; do
  if [[ "$PULL" -eq 1 ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "拉取数据库镜像 ${image}"
    docker pull "$image"
  else
    echo "数据库镜像已存在: ${image}"
  fi
done

if [[ "$WITH_EMBEDDING" -eq 1 ]]; then
  if [[ "$PULL" -eq 1 ]] || ! docker image inspect "ollama/ollama:latest" >/dev/null 2>&1; then
    echo "拉取镜像 ollama/ollama:latest"
    docker pull "ollama/ollama:latest"
  else
    echo "镜像已存在: ollama/ollama:latest"
  fi
  if [[ -z "${DOCKER_EMBEDDING_BASE_URL:-}" ]] && ! grep -q '^DOCKER_EMBEDDING_BASE_URL=.' .env 2>/dev/null; then
    export DOCKER_EMBEDDING_BASE_URL="http://ollama:11434"
  fi
fi

echo "构建应用镜像并安装代码依赖"
compose=(docker compose)
if [[ "$WITH_EMBEDDING" -eq 1 ]]; then
  compose=(docker compose --profile embedding)
fi
if ! "${compose[@]}" up -d --build --wait; then
  "${compose[@]}" ps || true
  "${compose[@]}" logs --tail=80 || true
  exit 1
fi

web_port="${WEB_PORT:-}"
if [[ -z "$web_port" ]]; then
  web_port="$(grep -E '^[[:space:]]*WEB_PORT=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '"' || true)"
fi
web_port="${web_port:-8080}"

echo "等待 http://127.0.0.1:${web_port}/office/health"
ready=0
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${web_port}/office/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done

if [[ "$ready" -ne 1 ]]; then
  echo "服务尚未通过健康检查。查看日志: docker compose logs --tail=80" >&2
  "${compose[@]}" ps
  exit 1
fi

echo "已启动。在浏览器打开 http://127.0.0.1:${web_port}"
if [[ "$WITH_EMBEDDING" -eq 1 ]]; then
  echo "首次使用向量检索时拉取模型: docker compose exec ollama ollama pull bge-m3"
fi

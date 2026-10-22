# 服务器部署 mcp-server（精简栈）

组成：`postgres` + `mcp-server` + `ollama` + `nginx`（仅 HTTP，按 IP 访问）。  
旧办公全栈 Docker 镜像与 SPA nginx 模板已移除；需要 HTTPS 时可用 `deploy/certbot/`。

## 首次启动

```sh
cd /apps/youjulab   # 或你的仓库路径
cp deploy/.env.mcp.example .env
# 编辑 .env：改 POSTGRES_PASSWORD、MCP_ACCESS_TOKEN、ACCOUNT_JWT_SECRET、APP_VERSION

docker compose up -d --build
docker compose exec ollama ollama pull bge-m3   # 首次拉向量模型，体积较大

curl -fsS http://127.0.0.1/health
curl -fsS http://127.0.0.1/version.json
```

桌面端远程地址填：`http://你的服务器IP`（不要加路径）。  
版本提示读：`http://你的服务器IP/version.json`。

## 手动更新

```sh
./deploy/deploy-mcp.sh          # 仅 mcp 相关文件变更时才 build
./deploy/deploy-mcp.sh --force  # 强制重建 mcp-server 与 nginx
```

## Gitee Webhook 自动部署

### 1. 服务器监听

任选一种长期跑法（systemd / screen / 后台）：

```sh
export DEPLOY_WEBHOOK_SECRET='换成随机长串'
# 可选：DEPLOY_WEBHOOK_PORT=9000 DEPLOY_BRANCH=main
python3 /apps/youjulab/deploy/webhook-mcp.example.py
```

防火墙只放行需要的来源；生产环境建议再套一层 HTTPS 或 SSH 隧道。

### 2. Gitee 后台

1. 开源仓 → **管理** → **WebHooks** → 添加  
2. URL：`http://SERVER_IP:9000/hooks/deploy-mcp`  
3. 密码：与 `DEPLOY_WEBHOOK_SECRET` 相同（请求头 `X-Gitee-Token`）  
4. 勾选事件：**Push**；若可限制分支，只选 `main`  
5. 保存后可用「测试」推送一次，看 `deploy/deploy-mcp.log`

### 3. 保护主分支（防他人直推触发部署）

1. 仓库 → **管理** → **仓库设置** / **保护分支**  
2. 保护 `main`：禁止普通人直接 push  
3. 仅允许 **Pull Request** 合并，且需你 **审核通过**  

未合并的 PR **不会** 触发对 `main` 的 Push，因此不会自动部署。

### 4. 路径过滤

即使 `main` 有 push，`deploy-mcp.sh` 也会检查变更路径；只有 `mcp-server/`、`docker-compose.yml`、`deploy/Dockerfile.nginx`、`deploy/nginx/mcp.conf.template`、`deploy/entrypoint-mcp.sh`、`deploy/postgres/` 相关变更才编译。改办公前端等会跳过。

## Ollama 说明

- 容器默认启动，**不映射**宿主机 11434。  
- mcp 通过 `EMBEDDING_BASE_URL=http://ollama:11434` 访问。  
- 模型在卷 `ollama_data`；代码更新不会清模型。  
- Webhook **不会**每次重拉 Ollama 镜像。

## 桌面「有更新」提示

发桌面安装包时 bump `.env` 里的 `APP_VERSION` 并 `docker compose up -d mcp-server`（或写入环境后重启）。桌面启动会对比 `/version.json`。安装包托管与自动下载升级另案处理，不在本脚本内。

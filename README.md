# 有据 youjulab

人和 AI 共用的文档工作台。工作区里可以写文档、表格、PDF、思维导图、日历和持仓；外部 Agent 通过同一套 MCP / HTTP 接口读写表格、文档和导图。

## 组成

| 服务 | 作用 | 默认地址 |
| --- | --- | --- |
| web | 浏览器工作区 | http://www.youjulab.com 与 https://www.youjulab.com |
| office-backend | 登录、项目、文档状态、模型编排 | 容器内 8100，经 `/office` 反代 |
| mcp-server | 财务数据、资讯、表格/文档/导图存储、持仓 | 容器内 8000，经 `/backend` 反代 |
| postgres | `finresearch` 业务库，以及带 pgvector 的 `office_vectors` | 仅容器网络 |
| mongo | 办公账号、项目和文档元数据 | 仅容器网络 |

PDF 向量检索使用 embedding 服务，默认连宿主机 `http://host.docker.internal:11434` 上的 Ollama（模型 `bge-m3`）。也可以用 Compose profile 在栈里启动 Ollama。

## Docker Compose

需要 Docker Engine 与 Docker Compose v2。

```sh
./deploy/up.sh
```

脚本会检查本机是否已有数据库镜像，没有则拉取 `pgvector/pgvector:pg16` 和 `mongo:7`，再构建应用镜像（安装 Python 与前端依赖）并启动。首次构建需要下载依赖，时间会比较长。

启动后打开 http://www.youjulab.com 或 https://www.youjulab.com ，注册邮箱账号即可使用。把域名解析到这台机器的 80 和 443。正式证书放在 `deploy/certs/fullchain.pem` 和 `deploy/certs/privkey.pem`；目录为空时容器会生成一张临时自签证书，浏览器会提示不受信任。

常用参数：

```sh
./deploy/up.sh --pull                 # 重新拉取数据库镜像
./deploy/up.sh --with-embedding       # 同时启动 Ollama
```

启用栈内 Ollama 后，首次做 PDF 向量检索前执行：

```sh
docker compose exec ollama ollama pull bge-m3
```

模型密钥、搜索密钥和访问令牌放在仓库根目录的 `.env`（可从 `.env.example` 复制）。Compose 会读取其中的密钥，并自行把数据库地址改写到容器网络，不必把 `127.0.0.1` 改成服务名。数据库口令默认 `youju` / `youju-dev-change-me`，可用 `POSTGRES_PASSWORD`、`MONGO_PASSWORD` 覆盖；口令请使用 URL 安全字符。改口令后要删除对应数据卷才会重新初始化。

```sh
docker compose logs -f
docker compose ps
docker compose down          # 停止并保留数据卷
docker compose down -v       # 同时删除数据库和上传文件
```

网页端口用 `WEB_HTTP_PORT`（默认 80）和 `WEB_HTTPS_PORT`（默认 443）修改。

## 本机直接运行

不使用容器时，在本机准备 MongoDB、带 pgvector 的 PostgreSQL，以及可选的 Ollama。复制 `.env.example` 为 `.env` 并填写连接与模型配置。

```sh
python3 -m venv .venv
.venv/bin/pip install -r mcp-server/requirements.txt -r apps/office-backend/requirements.txt
cd mcp-server && ../.venv/bin/python -m db.bootstrap && cd ..

FINANCE_MCP_TRANSPORT=http .venv/bin/python mcp-server/server.py
.venv/bin/python apps/office-backend/server.py

cd apps/office-copilot-web
npm ci
npm run dev
```

前端开发服务器是 http://127.0.0.1:5173 ，并把 `/office`、`/backend` 代理到本机 8100 和 8000。

财报补齐等维护命令见部署环境自己的运维说明；这些命令读取 `FINANCE_DATABASE_URL` 或 `FINANCE_DB_*`，在容器部署里可以对 `postgres` 服务执行。

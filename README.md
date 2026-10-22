# 有据 youjulab

人和 AI 共用的文档工作台。文档、表格、PDF 和向量索引在本机；注册登录、持仓、证券和 embedding 在 mcp-server。

## 组成

| 进程 | 作用 | 默认地址 |
| --- | --- | --- |
| office-copilot-web | 工作区界面 | http://127.0.0.1:5173 |
| office-backend | 本机项目、文档、PDF、sqlite-vec | http://127.0.0.1:8100 |
| mcp-server | 账号、持仓、证券、embedding、投研数据 | http://127.0.0.1:8000 |
| office-desktop | Electron 壳，拉起本机界面和 office-backend | 见 [apps/office-desktop/README.md](apps/office-desktop/README.md) |

远程地址和 embedding 都默认本机 `http://127.0.0.1:8000`。embedding 接口是该地址下的 `/api/embeddings`。部署到服务器时，在登录窗或设置里把远程地址改成服务器 URL，或改 `MCP_REMOTE_BASE_URL`。mcp-server 自己调用的向量模型默认本机 Ollama `http://127.0.0.1:11434`，换服务器或云厂商时只改 `EMBEDDING_BASE_URL`。

## 本机直接运行

复制 `.env.example` 为 `.env`。mcp-server 还需要 Postgres（`finresearch`）和它自己的环境变量，见 [mcp-server/.env.example](mcp-server/.env.example)。

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

前端开发服务器是 http://127.0.0.1:5173 ，并把 `/office` 代理到本机 8100。登录、持仓和 embedding 直接请求远程地址（默认 http://127.0.0.1:8000 ）。

未登录可以点「离线进入」使用本地文档。持仓、证券搜索和 PDF 向量索引需要登录远程账号。远程不可达时，登录框只保留「离线进入」。

## 服务器（mcp 精简栈）

仅部署 Postgres、mcp-server、Ollama 与 nginx（HTTP / IP）。步骤见 [deploy/MCP-DEPLOY.md](deploy/MCP-DEPLOY.md)。旧办公全栈 Docker 镜像与 SPA nginx 模板已移除；证书相关脚本仍保留在 `deploy/certbot/`。

## 桌面端

开发说明见 [apps/office-desktop/README.md](apps/office-desktop/README.md)。桌面进程负责启动本地 office-backend、记住远程地址和 PDF 目录，并在启动时对照远程 `version.json` 提示更新。

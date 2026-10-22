# youjulab Desktop

Electron 壳：本机 SQLite 文档和 sqlite-vec，并随应用拉起本机 Python MCP 服务。

桌面 MCP 默认绑定 `127.0.0.1:8000`；若端口被占用会选择并保存一个可用端口。下载外部 Agent Skill 时会自动写入当前本机 MCP 地址和用户令牌。登录、持仓和云端 embedding 的远程地址仍可在登录窗或设置中配置。

## 数据会丢吗？重新打包会覆盖吗？

**不会。** 打包只生成/替换 `release/.../youjulab.app`（程序 + 前端 + Python sidecar）。文档库、向量库、PDF 都在 `.app` 外面：

| 模式 | 路径 | 谁改会动到数据 |
|------|------|----------------|
| `userData`（开发与安装默认） | `~/Library/Application Support/youjulab-desktop/` | 删 userData；**重打包也不动这里** |
| `repo`（仅隔离调试） | 仓库 `data/office-local/`、`data/office-files/` | 仅 `YOUJU_DATA_MODE=repo` 时使用 |

`app-config.json`（含 `dataMode`、远程地址、本机 MCP 端口和仅供本机进程间调用的服务密钥）也在 userData 里，重打包不会清掉。服务密钥不会暴露给渲染进程。

开发与桌面**始终共用一套 userData 数据**。Electron 开发态与打包 app 都读写 `~/Library/Application Support/youjulab-desktop/`；旧的仓库 `data/` 仅用于隔离调试或一次性导入。

注意：

- **同一时刻只跑一个 office-backend**（不要 Vite 的 8100 和桌面壳各开一个写同一个 sqlite）。
- 仅在需要隔离调试时使用 `YOUJU_DATA_MODE=repo`；不要将它写入日常开发环境。

## Python sidecar

打包安装包内嵌两个 PyInstaller onedir：`youju-office-backend` 管理本地资产，`youju-mcp-server` 对本机开放 MCP/REST 办公工具。两者由 Electron 主进程统一启动、健康检查和停止，不依赖用户手工启动 Python 或 `.venv`。

```sh
cd apps/office-desktop
npm run build:services
# → sidecar/dist/youju-office-backend/
# → mcp-sidecar/dist/youju-mcp-server/
```

开发态若没有 sidecar，仍回退到仓库 `.venv` + 对应 Python 入口。也可用：

- `YOUJU_SIDECAR=/path/to/youju-office-backend` 强制指定可执行文件
- `YOUJU_MCP_SIDECAR=/path/to/youju-mcp-server` 强制指定 MCP 可执行文件
- `YOUJU_PYTHON=...` 强制指定解释器（无 sidecar 时）

## 开发

先起前端 Vite，再开桌面：

```sh
cd apps/office-desktop
npm install
npm run dev
```

Main 进程会：

1. 读取 `userData/app-config.json`（没有则按是否存在仓库 `data/` 选 `repo` / `userData`）
2. 选择稳定的本机 MCP 端口并拉起 MCP sidecar（若有）或仓库 `.venv` 中的 `mcp-server`
3. 拉起 Office sidecar（若有）或仓库 `.venv` 中的 `office-backend`，并把本机 MCP 地址传入 Skill 导出链路
4. 开发态打开 Vite `http://127.0.0.1:5173`；打包态打开本机 backend 根路径（同源 HTTP，避免 `file://` 拦远程）
5. 请求远程 `version.json`，版本不同则提示更新

开发态请把 Vite 的 `OFFICE_API_PORT` 指到 Electron 打印的 backend 端口。

## 打包

一键构建 macOS 桌面目录包：

```sh
cd /Users/zackwang/CursorProjects/youjulab/apps/office-desktop
npm run build:desktop
open release/mac-arm64/youjulab.app
```

该命令会依次构建前端、Office sidecar、MCP sidecar，并生成可直接打开的 `.app`。

目录包（可直接打开 `.app`）：

```sh
cd apps/office-copilot-web && npm run build
cd ../office-desktop
npm run build:services
npm run pack:mac
open release/mac-arm64/youjulab.app
```

打 dmg（会先构建 sidecar）：

```sh
cd apps/office-desktop
npm run dist:mac:full
```

产物在 `release/`，含 Electron 壳、前端 `web/`、`sidecar/youju-office-backend/` 与 `mcp-sidecar/youju-mcp-server/`。本机 MCP 会随桌面程序启动。

本机 MCP 收口 Office 工具与个人金融数据；云端 embedding 仍沿用配置的上游服务。PostgreSQL 保留为服务端/团队部署的可选模式，不会把数据库凭据打进桌面安装包。

桌面 MCP 默认把账号、持仓、行情缓存、任务记录、搜索配额和证券/财报索引存入同一用户数据目录的 `finresearch.sqlite`，并启用 WAL、外键和写入等待。服务器端仍可用 `FINANCE_STORE=postgres` 保持 PostgreSQL 模式。

## 从 PostgreSQL 迁移

先配置源库连接 `FINANCE_DATABASE_URL`，再指定目标文件并执行：

```sh
cd /Users/zackwang/CursorProjects/youjulab/mcp-server
FINANCE_DATABASE_URL='postgresql://...' \
FINANCE_SQLITE_PATH="$HOME/Library/Application Support/youjulab-desktop/finresearch.sqlite" \
../.venv/bin/python -m db.migrate_postgres_to_sqlite --replace
```

迁移会保留账号、证券、财报、持仓、行情缓存、净值、计划任务和搜索配额。执行前请退出桌面程序；`--replace` 会清空目标 SQLite 对应表后再导入。

注意：不要在 `npm run ...` 同一行后面加 `# 注释`；部分环境下 `#` 会进 electron-builder 参数并报 `Unknown target: #`。

## 拷贝到独立 userData（可选）

首次改为统一 `userData` 时，用它将仓库中的旧工作数据带入 userData（原 userData 会先备份）：

```sh
apps/office-desktop/scripts/import-dev-data.sh
```

日常开发与桌面应用都会自动使用同一份 userData，不必反复导入。

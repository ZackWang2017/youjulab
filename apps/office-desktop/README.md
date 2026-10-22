# youjulab Desktop

Electron 壳：本机 SQLite 文档和 sqlite-vec，远程 mcp 做登录、持仓和 embedding。

远程地址默认 `http://127.0.0.1:8000`。部署到服务器后，在登录窗或设置里修改；不改则继续用本机。

## 数据会丢吗？重新打包会覆盖吗？

**不会。** 打包只生成/替换 `release/.../youjulab.app`（程序 + 前端 + Python sidecar）。文档库、向量库、PDF 都在 `.app` 外面：

| 模式 | 路径 | 谁改会动到数据 |
|------|------|----------------|
| `repo`（本机开发默认） | 仓库 `data/office-local/`、`data/office-files/` | 改仓库 `data/`；**重打包不动这里** |
| `userData`（外发安装） | `~/Library/Application Support/youjulab-desktop/` | 删 userData；**重打包也不动这里** |

`app-config.json`（含 `dataMode`、远程地址）也在 userData 里，重打包不会清掉。

开发与桌面**共用一套数据**：把 `dataMode` 设为 `repo`（本机若已有 `data/office-local/office.sqlite`，首次启动默认就是 `repo`）。此时 Vite 开发态与打包 app 读写同一套库。

注意：

- **同一时刻只跑一个 office-backend**（不要 Vite 的 8100 和桌面壳各开一个写同一个 sqlite）。
- 切换 `dataMode` 后需**重启桌面应用**。
- 环境变量可强制：`YOUJU_DATA_MODE=repo|userData`。

## Python sidecar

打包安装包内嵌 PyInstaller 打出的 `youju-office-backend`（onedir），启动时优先用它，不依赖本机 `.venv`。

```sh
cd apps/office-desktop
npm run build:sidecar   # → sidecar/dist/youju-office-backend/
```

开发态若没有 sidecar，仍回退到仓库 `.venv` + `apps/office-backend/server.py`。也可用：

- `YOUJU_SIDECAR=/path/to/youju-office-backend` 强制指定可执行文件
- `YOUJU_PYTHON=...` 强制指定解释器（无 sidecar 时）

## 开发

先起前端 Vite 和 mcp-server（仓库根目录），再开桌面：

```sh
cd apps/office-desktop
npm install
npm run dev
```

Main 进程会：

1. 读取 `userData/app-config.json`（没有则按是否存在仓库 `data/` 选 `repo` / `userData`）
2. 拉起 sidecar（若有）或仓库 `.venv` 中的 `office-backend`
3. 开发态打开 Vite `http://127.0.0.1:5173`；打包态打开本机 backend 根路径（同源 HTTP，避免 `file://` 拦远程）
4. 请求远程 `version.json`，版本不同则提示更新

开发态请把 Vite 的 `OFFICE_API_PORT` 指到 Electron 打印的 backend 端口。

## 打包

目录包（可直接打开 `.app`）：

```sh
cd apps/office-copilot-web && npm run build
cd ../office-desktop
npm run build:sidecar
npm run pack:mac
open release/mac-arm64/youjulab.app
```

打 dmg（会先构建 sidecar）：

```sh
cd apps/office-desktop
npm run dist:mac:full
```

产物在 `release/`，含 Electron 壳、前端 `web/`、`sidecar/youju-office-backend/`。远程 mcp 默认 `http://127.0.0.1:8000`。

注意：不要在 `npm run ...` 同一行后面加 `# 注释`；部分环境下 `#` 会进 electron-builder 参数并报 `Unknown target: #`。

## 拷贝到独立 userData（可选）

仅在需要 `userData` 隔离副本时使用（例如模拟外发安装）：

```sh
apps/office-desktop/scripts/import-dev-data.sh
```

然后在设置里把「本机数据位置」改成「独立副本」。日常共用请用 `repo` 模式，不必反复导入。

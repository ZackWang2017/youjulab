# youjulab Desktop

## Storage And Services

- Electron starts only the local Python office-backend, bound to loopback.
- Office assets, PDF originals, parsed text/layout and sqlite-vec vectors remain local.
- Accounts, portfolios, financial data, news and scheduled task records use the independent
  remote Python MCP service with PostgreSQL.
- Embeddings are generated through remote MCP `/api/embeddings`; returned vectors
  are stored and searched locally. Original PDF files are not uploaded for indexing.
- LLM prompts and embedding text are still sent to the configured remote providers.
- Desktop clients neither connect to PostgreSQL nor carry its credentials.

Default MCP address: `http://192.168.0.5:8000`. Change it in the login connection
settings or Settings. It persists in `app-config.json`, outside the application package.
A server-address change applies to subsequent requests without restarting the local backend.
Re-login after switching servers. Older bundled-MCP loopback defaults migrate to the LAN default;
custom server addresses remain unchanged.

## Local User Data

Development and packaged builds use the same OS user-data directory:

| OS | Default directory |
|---|---|
| macOS | `~/Library/Application Support/youjulab-desktop/` |
| Windows | `%APPDATA%/youjulab-desktop/` |
| Linux | `$XDG_CONFIG_HOME/youjulab-desktop/` or `~/.config/youjulab-desktop/` |

Files: `office.sqlite`, `office-vectors.sqlite`, `office-files/`, `app-config.json`.
Separate OS users have separate directories; app users within a directory are filtered
by the existing Office account and asset-permission rules. This is not cross-device sync.

Repackaging does not replace these files. Do not run two Office backends against the same files.
The legacy local `finresearch.sqlite` is retained for rollback/migration but is no longer
read or updated by the desktop application.

For direct Python development, leave Office paths unset to use OS userData, or explicitly set
`OFFICE_SQLITE_PATH`, `OFFICE_VECTOR_SQLITE_PATH`, `OFFICE_FILE_ROOT` consistently.
`YOUJU_DATA_MODE=repo` remains an explicit isolated debugging option.

## Development

Start the independent MCP server yourself, with PostgreSQL and its embedding provider configured.
See [multiuser-desktop.md](../../deploy/multiuser-desktop.md).

```sh
cd apps/office-copilot-web
npm run dev
```

In another terminal:

```sh
cd apps/office-desktop
npm run dev
```

Electron starts only Office, not MCP, PostgreSQL or Ollama.
When the remote service is unavailable, the local UI can still load and show connection errors.

## Build Commands

### macOS 一键重建、安装、重启

保存编辑内容后运行：

```sh
cd /Users/zackwang/CursorProjects/youjulab/apps/office-desktop
npm run rebuild:install:mac
```

脚本会请求正常退出 App，等待本地后台结束，完整构建 Web、Python sidecar 和
`.app`，校验暂存副本后替换 `/Applications/youjulab.app`，最后启动新版。
旧 App 直接删除替换，不创建备份，也不提供自动回滚。
不会创建 DMG，不清理 userData，不强制杀进程。
构建失败会停止，不替换已安装版本（App 已退出，需要自行重新打开）。
默认管理和更新地址为 `http://192.168.0.5:8200`，可通过导出的
`YOUJU_RELEASE_BASE_URL` 和 `YOUJU_ADMIN_BASE_URL` 覆盖。
默认会询问是否已保存内容，确认后继续；无人值守可用
`npm run rebuild:install:mac -- --yes`，调用方必须先确保没有未保存内容。

Local testing also accepts HTTP on localhost or private LAN IPv4 addresses, for example:

```sh
export YOUJU_RELEASE_BASE_URL=http://192.168.0.5:8200
export YOUJU_ADMIN_BASE_URL=http://192.168.0.5:8200
```

Set the server's `ADMIN_PUBLIC_URL` to the same address and `RELEASE_COOKIE_SECURE=0`
for this test; otherwise generated download URLs still point to the production domain.
HTTP is unencrypted. Restore HTTPS URLs and secure cookies before production deployment.

These commands are for manual use. They are not required for ordinary development.

```sh
cd apps/office-desktop
YOUJU_RELEASE_BASE_URL=http://192.168.0.5:8200
YOUJU_ADMIN_BASE_URL=http://192.168.0.5:8200
npm run build:desktop
```

This builds the Web UI and Office sidecar, then creates a macOS directory application.
For a DMG: `npm run dist:mac:full`.
For Windows, build the Office sidecar on Windows before `electron-builder --win`.
The package contains no MCP sidecar. Legacy `mcp-sidecar/` build sources are not used.

Overrides: `YOUJU_SIDECAR`, `YOUJU_USE_SIDECAR=1`, `YOUJU_PYTHON`.
The remote server must be deployed independently.

## Backup Management

Backups first save the current user's local Office databases and PDFs to the configured
local directory, then upload a verified copy to the independent
[admin-server](../admin-server/README.md). Remote portfolios are not included.
Set `YOUJU_ADMIN_BASE_URL` when building; it falls back to `YOUJU_RELEASE_BASE_URL`.
Development may use an explicit loopback HTTP address; production requires HTTPS.
The server's backup directory is configured only on the server, never by the client.

The existing `office.sqlite` stores a user/device-scoped backup catalog with monotonic
versions, hashes and upload status. Restore checks server metadata and uses a verified
local copy when available, otherwise downloads and verifies the remote copy first.
Historical restore preserves the explicitly selected version. Offline latest-version
checks fail clearly; an explicit local historical restore remains available.
Upload failures keep local archives and expose Retry Upload. Progress is shown for each
stage, including the protective backup made before restore. Old user-isolated archives
remain available locally; create a new backup to upload in the new format.

Initialize the new admin-server tables manually as documented before using remote backup.
No PostgreSQL credentials are packaged into the client. Repackaging preserves device ID,
local paths and automatic-backup preferences in userData.

## Background Mode

macOS menu-bar and Windows notification-area tray icons stay available while the app runs.
Left click restores the current window; right click exposes only “退出程序”. Closing a window
hides it and keeps Office and scheduled backups running. Explicit Quit cleans up local service
processes. macOS Dock activation and Windows second launch restore the existing instance;
the Windows taskbar jump list also provides Quit. Only one instance can use userData at a time.

## Windows Installer

Build on **Windows x64**, with Node.js/npm and Python 3.12 x64 installed. Windows Python
sidecars cannot be produced by copying the macOS binary. From PowerShell:

```powershell
cd apps\office-desktop
$env:YOUJU_RELEASE_BASE_URL=http://192.168.0.5:8200
$env:YOUJU_ADMIN_BASE_URL=http://192.168.0.5:8200
npm run build:windows
```

The script creates `sidecar/.venv-win`, installs dependencies, builds the Web UI and frozen
Office backend, runs a temporary SQLite backup smoke check (no service), and creates an NSIS
installer under `release/`. To use an explicit Python interpreter:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-windows.ps1 -Python 'C:\Python312\python.exe'
```

`-SkipInstall` reuses already-installed dependencies. With a prepared Windows sidecar,
`npm run dist:win` builds just the installer; a packaging guard rejects missing/non-PE sidecars.
The wizard defaults to a per-user install, allows choosing a directory, creates Desktop/Start
Menu shortcuts and preserves userData on uninstall. No Python/PostgreSQL/MCP installation is
required on end-user machines. Unsigned installers may show SmartScreen/UAC warnings.

## Version Updates

Set `YOUJU_RELEASE_BASE_URL` **when packaging**. `electron-builder.config.cjs` validates HTTPS
and writes the address into the packaged application metadata. It is independent of the MCP
address, cannot be changed by user settings, and contains no database/admin credentials.
Without the variable, version checking is disabled with a build warning. Development runs
never download or install real updates.

The startup gate appears before Office starts. Optional updates can be skipped; forced updates
block the workspace. Downloads stay inside the app and are verified against size/SHA-512.
Windows NSIS updates install and restart; macOS downloads a DMG that the user opens and installs
manually. Downloading is not treated as installation. No custom macOS app replacement or
Gatekeeper bypass is used. First install of this new update-capable build is manual.

With no cached force policy, an unavailable server does not block startup; a previously confirmed
forced version remains blocking offline. An online response withdrawing the update clears it.
Only a newer matching stable version is offered; installing a version at least as new removes
the block. Windows currently uses unsigned packages (`verifyUpdateCodeSignature: false`), pinned
to the embedded HTTPS origin and SHA-512. This is not publisher code-signing: secure the release
administrator and TLS deployment. When signing Windows releases later, restore publisher verification.

Deploy the independent [admin-server](../admin-server/README.md) with its own PostgreSQL
database on the existing database instance. Legacy MCP `/version.json` is no longer used by desktop.

## Backup And Restore

Desktop backups contain only the signed-in user's local Office rows, PDF files and vectors,
not other users' records, login credentials, remote accounts or portfolios. The archive is
a user-scoped export, never a copy of the shared SQLite files.
Backup folders, records, schedule settings and last-backup times are isolated by server/account.
Logging out stops automatic backup. Restore requires current-password verification and first
creates a user-scoped pre-restore backup, then replaces only that user's records and PDF folder.
Other users' records, files and current credentials remain intact.
Legacy full-database archives are retained but not listed or restored by the new user-scoped UI.
Server operators must separately back up PostgreSQL, for example with `pg_dump`.

SQLite snapshots use the bundled Python runtime, including on Windows without sqlite3.exe.

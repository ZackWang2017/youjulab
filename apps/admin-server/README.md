# 有据在线管理服务

工程已由 `apps/release-server` 更名为 `apps/admin-server`。
FastAPI + 服务端 Jinja2 页面 + PostgreSQL，同时提供版本发布和客户端备份管理。

## 配置与部署

### 本工作区的正式配置

已创建本目录私有 `.env`（权限 600，Git 忽略），连接 MCP 相同的 PostgreSQL
实例 `localhost:5432`，使用独立数据库 `admin-server` 和独立角色 `youju_admin`。
数据库密码与会话密钥已随机生成，不复制 MCP 的管理员权限。
尚未创建真实数据库或角色；需按下方初始化步骤执行。
安装包存储目录为 `/Users/zackwang/WorkSpace/BaiduSynSpace/youjulab/release`，
客户端备份目录为 `/Users/zackwang/WorkSpace/BaiduSynSpace/youjulab`。

首次部署先设置自己的管理员密码（至少 12 位）：

```sh
cd /Users/zackwang/CursorProjects/youjulab/apps/admin-server
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python security.py
```

将输出的完整哈希填入 `.env` 的 `ADMIN_PASSWORD_HASH`，建议用单引号包裹，
避免手动通过 shell 导入时 `$` 被展开。确认 `ADMIN_PUBLIC_URL` 对应实际配置好的
域名与 HTTPS 反向代理；当前填写的是 `https://admin.youjulab.com`，不代表 DNS 已生效。

首次建库或升级表结构（手动执行，管理员 DSN 请替换成自己的凭据）：

```sh
ADMIN_BOOTSTRAP_DATABASE_URL='postgresql://zackwang@localhost:5432/postgres' .venv/bin/python bootstrap.py --apply
```

正常启动命令：

```sh
cd /Users/zackwang/CursorProjects/youjulab/apps/admin-server
.venv/bin/python server.py
```

`server.py` 和 `bootstrap.py` 都自动读取本目录 `.env`，无需 `source .env`。
终端中已导出的同名变量优先，避免遗留环境变量覆盖配置。
服务默认监听 `127.0.0.1:8200`，交由本机 Nginx 代理 HTTPS。

当前工作区为了通过 `http://192.168.0.5:8200` 本地调试，将私有 `.env` 中
`RELEASE_COOKIE_SECURE` 设为 `0`。HTTP 无法使用 Secure Cookie，否则登录提交
会出现“页面验证已失效”。修改后需自行重启服务并重新打开登录页。
这也适用于 localhost HTTP，不能通过只换 localhost 地址绕过 Secure Cookie 设置。
正式通过 HTTPS 部署前必须恢复为 `1`；不要通过不受信任网络的 HTTP 传输管理员密码。
`ADMIN_PUBLIC_URL` 仍是正式下载域名，应在实际部署时确认，局域网调试不会配置 DNS。

参考本目录 `.env.example` 创建私有 `.env`，安装 `requirements.txt`。
管理员仍固定为 `admin`，通过 `python security.py` 生成密码哈希，填入
`ADMIN_PASSWORD_HASH`；`ADMIN_SESSION_SECRET` 使用持久化的随机值，至少 32 字符。

- `ADMIN_DATABASE_URL`：新的专用库 `admin-server`，与 MCP 共用 PostgreSQL 程序。
- 已部署的旧库无需改名或搬数据：继续使用原 `release-server` DSN 即可。
- 兼容原 `RELEASE_DATABASE_URL`、会话、管理员密码及公网地址环境变量。
- `ADMIN_PUBLIC_URL`：管理及下载服务的 HTTPS 根地址，现有版本 API 路径不变。
- `ADMIN_BACKUP_ROOT`：客户端备份硬盘目录，默认
  `/Users/zackwang/WorkSpace/BaiduSynSpace/youjulab`。
- `ADMIN_ACCOUNT_BASE_URL`：固定 MCP 账号认证地址，默认 `http://192.168.0.5:8000`。
- `RELEASE_FILE_ROOT`：桌面安装包目录，与客户端备份目录独立。

明确初始化或升级表结构（包括新增 `client_backups` 表），不会自动执行：

```sh
ADMIN_BOOTSTRAP_DATABASE_URL='postgresql://ADMIN:YOUR_PASSWORD@127.0.0.1:5432/postgres' python bootstrap.py --apply
```

管理员 DSN 只供这一次初始化使用，不放入运行服务环境。专用服务角色不得使用超级用户权限。
脚本使用 SQL Identifier 正确引用带连字符的库名，拒绝接管其他角色拥有的数据库。
只创建管理库结构，不更改 MCP 业务数据。通过 pg_hba.conf 限制服务角色访问其他数据库。

手动启动：`python server.py`，默认 `127.0.0.1:8200`。
请由你自行启动；本次代码修改不会启动服务、建库或创建真实备份目录。

## 客户端备份

客户端先保存用户隔离的本地压缩包，并在现有 `office.sqlite` 的
`local_backup_records` 表中记录编号、设备、版本序号、路径、大小、SHA-256 及同步状态，
再上传一份服务器副本。备份记录不作为 Office 资产导出，恢复不会回滚记录序号。

服务器使用客户端登录令牌向固定 MCP `/api/account/me` 验证身份，不信任客户端传来的账号。
服务端按账号哈希和设备 ID 分目录保存，文件名由服务端生成，备份只在上传完成、
哈希与压缩包说明核对一致后进入 PostgreSQL。
管理页 `/admin/backups` 展示账号、设备、版本、大小和上传完成时间，不远程操作客户端恢复。

同一账号同一设备的版本序号是不可变的；相同编号及内容可以幂等重试，内容冲突则拒绝。
上传失败时本地文件不删除，客户端明确显示“本地已保存，待上传”，提供“重试上传”。
没有配置服务器、离线、认证失效、磁盘写入失败均不能显示成“双端备份成功”。

恢复最新版先同步服务器记录，按版本序号判断，不按文件修改时间判断：
本地存在同一份且 SHA-256 和大小一致时直接用本地；否则下载临时文件，验证后才替换缓存。
明确选择历史备份时恢复那一份，不自动替换成最新。
有服务器配置但离线时，不谎称已确认“最新”；用户仍可明确选择一份完整本地历史备份。
恢复前先创建保护性备份，再停止 Office 后台恢复本用户数据，其他用户和远端持仓不受影响。

备份记录默认限当前设备，不提供跨设备 Office 同步。设备编号在 app-config.json 持久保存，
重新打包不会改变。旧版用户隔离备份可导入本地历史记录，但需要新建备份才能上传服务器。
旧整库备份不纳入此流程。

## 客户端接口

- `GET /api/v1/backups?device_id=…`：当前登录账号、设备的备份记录。
- `PUT /api/v1/backups/{id}`：流式上传，携带设备、序号、创建时间、类型、文件名及 SHA-256。
- `GET /api/v1/backups/{id}/download?device_id=…`：身份验证后的备份下载。

这些接口使用 MCP Bearer 用户令牌，不使用后台管理员 Cookie。
版本与客户端备份均按原 HTTPS 根地址提供；桌面打包配置
`YOUJU_ADMIN_BASE_URL`，未单独设置时兼容使用 `YOUJU_RELEASE_BASE_URL`。
开发态可以显式配置 `YOUJU_ADMIN_BASE_URL=http://127.0.0.1:8200`，不写入正式包。

## 版本发布

公开下载页：`GET /release`，无需登录，按版本号倒序列出所有已发布版本、
更新说明和各平台安装包。草稿和已撤回版本不展示；下载安装包同样无需登录。
例如局域网访问 `http://192.168.0.5:8200/release`。

在 `/admin` 新建稳定语义版本（例如 `1.2.3`），上传 Mac arm64/x64 DMG 或 Windows x64
NSIS EXE，发布后再设为最新。发布后不能替换安装包；只可删除草稿。
撤回最新版本时明确选择替代版本或暂停更新。
原 `/api/v1/releases/latest`、历史查询、安装包及 Windows 更新元数据接口均保留。
客户端版本检查与强更流程不因服务改名而改变。

## 安全与存储

正式环境要求 HTTPS，安全 Cookie、CSRF、管理员登录限流保持开启。
仅本机调试可设置 `RELEASE_COOKIE_SECURE=0` 与本机 HTTP 公网地址。
一个服务 worker；更大部署应把登录限流移到代理层。
MCP HTTP 认证地址目前只适用于受信任局域网，跨公网请配置 HTTPS。

客户端备份默认上传限制为 20GB，安装包为 2GB。
Nginx 需要 `client_max_body_size 20500m`、`proxy_request_buffering off` 及足够的上传超时。
备份目录不可作为公开静态目录，所有下载必须经过身份验证。
权限目录 0700、文件 0600；不承诺备份内容已加密，服务器硬盘和百度同步目录权限应单独保护。
服务器必须有足够的暂存空间；中断残留的 .upload 只能在确认服务停止后清理。
通过 pg_dump 备份管理数据库，同时备份安装包和客户端备份目录。

## 验证

```sh
python -m unittest discover -s tests -v
```

测试使用临时文件和测试替身，不连接或创建真实管理数据库。

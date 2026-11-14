# 本地资料库资讯

资讯入口位于左侧“资料库”中，与 PDF、文档等入口一同折叠。
资讯组与其他资料库资产一致，归属于用户和项目，并关联 `kind=information` 的 Office 资产。
左侧资讯下拉显示当前项目的资讯组，最近打开使用既有按用户、项目隔离的资产记录。
“今日资讯 / 深入点评”位于内容工具栏；每个项目有自己的“资讯收藏栏”。
切换账号或项目后重新读取数据，不复用其他项目的列表。

## 存储与代码

- `repository/`：SQLite 仓库、来源清单及 RSS / 交易所公告适配器。
- `service/`：组配置、今日获取、去重、收藏、影响判断及点评流程。
- `endpoints/`：`/api/information/*`，复用 Office 登录权限。
- `collectors/`：本地财经来源、证券搜索、文章读取、微信读书会话。
- `skills/`：AI 提示词，随 Python sidecar 打包，不再依赖仓库根目录。
- `backup.py`：按用户导出和恢复资讯记录，接入既有本地备份。

组配置、条目、收藏状态和点评存于既有 `OFFICE_SQLITE_PATH`（默认 userData
下的 `office.sqlite`）中的 `information_groups`、`information_items`、
`information_reviews`，首次使用自动建表。外键包含用户 ID，禁止跨用户关联。
运行时不连接远程 PostgreSQL，不自动导入历史 MCP 资讯数据，也不删除远程历史表。
MCP 不再注册资讯模块接口；修改后的 MCP 部署需自行重启才会移除旧进程中的路由。

持仓股票选择器仍按需调用远程 portfolio 服务，使用当前用户远程令牌；
未连接时只影响持仓选择，不影响读取已保存的本地资讯。
财经、RSS、公告和文章请求由本机执行，因此需本机能访问相应站点。
AI 继续使用当前用户的模型设置；本地存储不代表模型推理离线运行。

## 配置与登录

资料库中的组编辑窗口负责配置来源、关键字及持仓股票，配置保存到本地 SQLite。
内置 RSS / 财经来源不需要服务器侧配置。可选 RSSHub 环境变量
`DIGEST_RSSHUB_BASE` 需传给本地 Office 后台进程，而不是仅配置远程 MCP。

微信读书先用 HTTP 获取扫码登录，二维码仅在用户获取公众号且登录失效时展示。
Cookie 保存到 `userData/information/weread/<用户ID的SHA256>/session.json`，
不会使用或复制旧 MCP 的登录态。账号间的扫码会话和 Cookie 隔离。
Cookie 与浏览器缓存不进入数据备份，换机或清除登录态后重新扫码。

可选浏览器回退：启动本地后台前配置 `OFFICE_WEREAD_BROWSER=1`，并提供
`OFFICE_WEREAD_BROWSER_EXECUTABLE` 或 `OFFICE_WEREAD_BROWSER_CHANNEL=chrome`。
此模式还需安装 Playwright 和可用浏览器；默认桌面包不额外下载或捆绑浏览器。
来源网站接口可能变动，抓取与扫码的真实外网行为需单独验证。

## 备份与升级

新备份包括当前用户的资讯组、文章、收藏及点评；恢复时不覆盖其他用户。
旧备份不含资讯字段时保留当前资讯，避免旧格式恢复无意清空新模块。
新的远端数据库迁移脚本明确跳过历史资讯表，并在报告中列出跳过数量。

迁移涉及 Python 后台和打包依赖，桌面更新需完整重建，不能只同步网页资源。

## 显式导入历史数据

运行时不自动从远程导入，但开发者可使用一次性工具（需 PostgreSQL Python 依赖）：

```sh
# 在仓库根目录执行
PYTHONPATH=apps/office-backend .venv/bin/python -m information.migrate_postgres \
  --project '邮箱=本地项目ID'
```

默认仅预检，读取根目录 `.env` 的 `FINANCE_DATABASE_URL` 或 `FINANCE_DB_*`。
确认报告并退出桌面端后，添加 `--apply` 执行。多个账号可重复传入 `--project`；
仅一个本地项目的账号可自动选择，有多个项目时必须明确指定。
也可用 `--create-project '邮箱=历史资讯'` 新建独立历史项目；预检不创建真实项目，
正式导入才创建，并且重复执行会复用同名项目。
源 PostgreSQL 事务强制只读；本地先生成 `migration-backups/information-时间戳/office.sqlite`
快照，再单事务合并。通过邮箱关联本地账号，重映射组、条目和点评引用 ID，创建正常
资料库资产，保留已有本地内容，重复执行不重复插入。不迁移账号密码或微信读书 Cookie。

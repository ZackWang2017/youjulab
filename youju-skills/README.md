# 外部 Agent 用的表格和文档 skill。与仓库根目录 skills/（协作助手提示词）分开。
# 不区分具体 Agent：能连 MCP 用 MCP，只能发 HTTP 用同一进程上的 REST。
#
# 入口进程：桌面端 office-backend（默认 http://127.0.0.1:8100），不是远程 mcp-server。
#
# 表格：项目名 project_name、表名 table_name、sheet 名 sheet_name。
# 入口：/mcp/sheets 与 /api/spreadsheets/*
#
# 文档：项目名 project_name、文档名 document_name。
# 入口：/mcp/documents 与 /api/documents/*
#
# 绘图：/mcp/diagrams 与 /api/diagrams/{create,read,write}，直连 office-backend。
# 支持结构化 graph / 原生 draw.io XML；整图更新必须带 expected_revision。
# 部署公网 MCP 时，OFFICE_PUBLIC_BASE_URL 应设置为真实 office-backend 公网地址，
# 它同时用于下载 Skill 的地址替换和绘图 MCP 的 Host/Origin 白名单。
#
# 鉴权：32 位 token 只用来找到登录邮箱账号；项目挂在账号下，不挂在可轮换的 token 上。
# 下载：登录后点击右上角邮箱，下载已写入当前 token 与 __OFFICE_BASE_URL__ 的单文件 self-contained skill。

SKILL.md

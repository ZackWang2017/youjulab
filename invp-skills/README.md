# 外部 Agent 用的表格和文档 skill。与仓库根目录 skills/（协作助手提示词）分开。
# 不区分具体 Agent：能连 MCP 用 MCP，只能发 HTTP 用同一进程上的 REST。
#
# 表格：项目名 project_name、表名 table_name、sheet 名 sheet_name。
# 入口：/mcp/sheets 与 /api/spreadsheets/*
#
# 文档：项目名 project_name、文档名 document_name。
# 入口：/mcp/documents 与 /api/documents/*
#
# 鉴权：32 位 token 只用来找到登录邮箱账号；项目挂在账号下，不挂在可轮换的 token 上。
# 下载：登录后点击右上角邮箱，下载已写入当前 token 的 skill。

SKILL.md

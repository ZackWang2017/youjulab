# 外部厂商 Agent 用的表格和文档 skills。与仓库根目录 skills/（协作助手提示词）分开。
# 每个子目录对应一个 agent 厂商。
#
# 表格：项目名 project_name、表名 table_name、sheet 名 sheet_name。
# 工具：create/read/write_spreadsheet，merge/unmerge_cells，set_cell_background/border/font
# 入口：/mcp/sheets 与 /api/spreadsheets/*
#
# 文档：项目名 project_name、文档名 document_name。
# 工具：create/read/write_document，format_document
# 入口：/mcp/documents 与 /api/documents/*
#
# 鉴权：32 位 token 只用来找到登录邮箱账号；项目挂在账号下，不挂在可轮换的 token 上。
# 下载：登录后点击右上角邮箱，下载已写入当前 token 的 skill。

invp-skills-chatgpt/     ChatGPT / Custom GPT
invp-skills-workbuddy/   WorkBuddy
invp-skills-deepseek/    DeepSeek

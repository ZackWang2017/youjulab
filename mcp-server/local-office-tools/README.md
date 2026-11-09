# local-office-tools（部分已迁出）

表格 / 文档 / 导图 / 项目计划 / PDF 索引与大纲的外部 Agent 入口已迁到
`apps/office-backend/agent_tools/`，由桌面 office-backend 直连 Store 对外提供
`/mcp/sheets|documents|mindmaps|project-plans` 与对应 `/api/*`。

本目录仍保留选区 Agent（`office_agent_*`、`office_context`、`office_patch`、`office_model`）
供 mcp-server `/api/office` 使用，不转发到本机 office 资产库。

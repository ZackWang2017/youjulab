---
name: invp-skills-deepseek
description: Read, write, merge, and format Invp office spreadsheets and documents from DeepSeek via mcp-server.
---

# Invp 表格读写（DeepSeek）

用 **同一 mcp-server 进程** 的表格入口创建、读取、写入、合并单元格、设置背景色 / 边框 / 字体。DeepSeek 用函数调用时优先走 REST；若环境支持 MCP，再用 `/mcp/sheets`。不要调用财务 `/mcp`（指标、报表结构、PDF 解析/渲染），也不要走 `/api/office` 选区 Agent。

## 连接

- 表格 REST：`http://127.0.0.1:8000/api/spreadsheets/create`、`/read`、`/write`、`/merge`、`/unmerge`、`/background`、`/border`、`/font`
- 表格 MCP：`http://127.0.0.1:8000/mcp/sheets`
- 每个请求带：`Authorization: Bearer __ACCESS_TOKEN__`
- 令牌只用来找到你的登录邮箱账号。项目、表格挂在该账号下，不挂在令牌上。重新生成令牌后资产仍在，必须用新 skill 替换旧的。

财务 MCP 仍是 `http://127.0.0.1:8000/mcp`，不要混用。

## 工具

输入一律用名称，不要用内部 ID。

| 函数 | REST | 说明 |
| --- | --- | --- |
| create_spreadsheet | POST /api/spreadsheets/create | 创建缺失的项目 / 表格 / 工作表。同名 sheet 已存在则失败。 |
| read_spreadsheet | POST /api/spreadsheets/read | 读取单元格。不自动创建。可传 `start_cell` / `end_cell`。 |
| write_spreadsheet | POST /api/spreadsheets/write | 从 `start_cell` 写入二维 `values`。名称不存在会先创建。写入会保留已有样式。 |
| merge_cells | POST /api/spreadsheets/merge | 合并 `start_cell` 到 `end_cell`。与已有合并重叠则失败。 |
| unmerge_cells | POST /api/spreadsheets/unmerge | 取消与该区域重叠的合并。 |
| set_cell_background | POST /api/spreadsheets/background | `color` 为 `#RRGGBB` 或 red/yellow 等；空或 none 清除。 |
| set_cell_border | POST /api/spreadsheets/border | `style`：none/thin/medium/thick/dashed/dotted/double；`sides`：all/outside/inside/top/bottom/left/right。 |
| set_cell_font | POST /api/spreadsheets/font | 只改提供的项：font_family、font_size、bold、italic、underline、strikethrough、color、align、vertical_align。 |

公共参数：`project_name`、`table_name`、`sheet_name`。范围参数：`start_cell`（默认 A1）、`end_cell`。

## DeepSeek 函数调用

把下面函数注册为 tools，收到调用后用 HTTP POST 打到对应 REST，Header 带本文件中的 Bearer token。`Content-Type: application/json`。公共字段：`project_name`、`table_name`、`sheet_name`。

```json
[
  {
    "type": "function",
    "function": {
      "name": "create_spreadsheet",
      "description": "按项目名、表名、sheet名创建表格。已存在同名 sheet 则失败。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "table_name": { "type": "string" },
          "sheet_name": { "type": "string" }
        },
        "required": ["project_name", "table_name", "sheet_name"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "read_spreadsheet",
      "description": "按项目名、表名、sheet名读取单元格。不自动创建。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "table_name": { "type": "string" },
          "sheet_name": { "type": "string" },
          "start_cell": { "type": "string", "default": "A1" },
          "end_cell": { "type": "string" }
        },
        "required": ["project_name", "table_name", "sheet_name"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "write_spreadsheet",
      "description": "按项目名、表名、sheet名写入二维 values。名称不存在会先创建。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "table_name": { "type": "string" },
          "sheet_name": { "type": "string" },
          "values": { "type": "array", "items": { "type": "array" } },
          "start_cell": { "type": "string", "default": "A1" }
        },
        "required": ["project_name", "table_name", "sheet_name", "values"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "merge_cells",
      "description": "合并 start_cell 到 end_cell。与已有合并重叠则失败。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "table_name": { "type": "string" },
          "sheet_name": { "type": "string" },
          "start_cell": { "type": "string" },
          "end_cell": { "type": "string" }
        },
        "required": ["project_name", "table_name", "sheet_name", "start_cell", "end_cell"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "unmerge_cells",
      "description": "取消与 start_cell:end_cell 重叠的合并。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "table_name": { "type": "string" },
          "sheet_name": { "type": "string" },
          "start_cell": { "type": "string" },
          "end_cell": { "type": "string" }
        },
        "required": ["project_name", "table_name", "sheet_name", "start_cell"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "set_cell_background",
      "description": "设置单元格背景色。color 为 #RRGGBB 或颜色名；空或 none 表示清除。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "table_name": { "type": "string" },
          "sheet_name": { "type": "string" },
          "start_cell": { "type": "string", "default": "A1" },
          "end_cell": { "type": "string" },
          "color": { "type": "string" }
        },
        "required": ["project_name", "table_name", "sheet_name", "color"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "set_cell_border",
      "description": "设置单元格边框。style 为 none/thin/medium/thick/dashed/dotted/double；sides 为 all/outside/inside。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "table_name": { "type": "string" },
          "sheet_name": { "type": "string" },
          "start_cell": { "type": "string", "default": "A1" },
          "end_cell": { "type": "string" },
          "style": { "type": "string", "default": "thin" },
          "color": { "type": "string", "default": "#000000" },
          "sides": { "type": "string", "default": "all" }
        },
        "required": ["project_name", "table_name", "sheet_name"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "set_cell_font",
      "description": "设置字体。只改提供的项：font_family、font_size、bold、italic、underline、strikethrough、color、align、vertical_align。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "table_name": { "type": "string" },
          "sheet_name": { "type": "string" },
          "start_cell": { "type": "string", "default": "A1" },
          "end_cell": { "type": "string" },
          "font_family": { "type": "string" },
          "font_size": { "type": "number" },
          "bold": { "type": "boolean" },
          "italic": { "type": "boolean" },
          "underline": { "type": "boolean" },
          "strikethrough": { "type": "boolean" },
          "color": { "type": "string" },
          "align": { "type": "string" },
          "vertical_align": { "type": "string" }
        },
        "required": ["project_name", "table_name", "sheet_name"]
      }
    }
  }
]
```

REST 路径对照：create_spreadsheet → `/create`，read_spreadsheet → `/read`，write_spreadsheet → `/write`，merge_cells → `/merge`，unmerge_cells → `/unmerge`，set_cell_background → `/background`，set_cell_border → `/border`，set_cell_font → `/font`。

## 示例

```
POST http://127.0.0.1:8000/api/spreadsheets/write
Authorization: Bearer __ACCESS_TOKEN__
Content-Type: application/json

{"project_name":"年度预算","table_name":"销售表","sheet_name":"Q1","start_cell":"A1","values":[["月份","金额"],["1月",100]]}
```

```
POST http://127.0.0.1:8000/api/spreadsheets/merge
Authorization: Bearer __ACCESS_TOKEN__
Content-Type: application/json

{"project_name":"年度预算","table_name":"销售表","sheet_name":"Q1","start_cell":"A1","end_cell":"B1"}
```

```
POST http://127.0.0.1:8000/api/spreadsheets/background
Authorization: Bearer __ACCESS_TOKEN__
Content-Type: application/json

{"project_name":"年度预算","table_name":"销售表","sheet_name":"Q1","start_cell":"A1","end_cell":"B1","color":"#FFFF00"}
```

MCP 等价调用：

```
merge_cells(project_name="年度预算", table_name="销售表", sheet_name="Q1", start_cell="A1", end_cell="B1")
set_cell_font(project_name="年度预算", table_name="销售表", sheet_name="Q1", start_cell="A1", bold=true, font_size=14, align="center")
```

## 不要做的事

- 不要调用 `get_indicators`、`get_statements`、`parse_pdf`、`run_office_agent`
- 不要把 HTML 或截图当表格内容；本接口只读写数据和样式，渲染由办公工作区完成
- 一次最多约 10000 个单元格；更大的表用 `start_cell` / `end_cell` 分块读，或分多次写入

## 文档

文档不要用表格函数。入口是 `http://127.0.0.1:8000/mcp/documents`，REST 是 `POST /api/documents/create|read|write|format`。鉴权仍用本文件的 Bearer token。参数是 `project_name` 和 `document_name`。

| 函数 | REST | 说明 |
| --- | --- | --- |
| create_document | POST /api/documents/create | 创建项目（若缺）和文档。同名文档已存在则失败。 |
| read_document | POST /api/documents/read | 返回 markdown 和 blocks。不自动创建。 |
| write_document | POST /api/documents/write | markdown 加 mode：replace / append / insert / update。insert 和 update 需要 block_index。名称不存在会创建。 |
| format_document | POST /api/documents/format | block_index 必填。block_type：paragraph、h1、h2、h3、bullet、ordered、blockquote、code。bold、italic、strike、code 只改提供的项。 |

```json
[
  {
    "type": "function",
    "function": {
      "name": "create_document",
      "description": "按项目名和文档名创建文档。同名已存在则失败。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "document_name": { "type": "string" }
        },
        "required": ["project_name", "document_name"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "read_document",
      "description": "按项目名和文档名读取 Markdown 和块列表。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "document_name": { "type": "string" }
        },
        "required": ["project_name", "document_name"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "write_document",
      "description": "用 Markdown 修改文档。mode 为 replace、append、insert 或 update。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "document_name": { "type": "string" },
          "markdown": { "type": "string" },
          "mode": { "type": "string", "enum": ["replace", "append", "insert", "update"] },
          "block_index": { "type": "integer" }
        },
        "required": ["project_name", "document_name", "markdown"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "format_document",
      "description": "设置文档某一个块的标题、列表、引用、代码块或加粗、斜体、删除线、行内代码。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "document_name": { "type": "string" },
          "block_index": { "type": "integer" },
          "block_type": { "type": "string", "enum": ["paragraph", "h1", "h2", "h3", "bullet", "ordered", "blockquote", "code"] },
          "bold": { "type": "boolean" },
          "italic": { "type": "boolean" },
          "strike": { "type": "boolean" },
          "code": { "type": "boolean" }
        },
        "required": ["project_name", "document_name", "block_index"]
      }
    }
  }
]
```

```
POST http://127.0.0.1:8000/api/documents/write
Authorization: Bearer __ACCESS_TOKEN__
Content-Type: application/json

{"project_name":"年度预算","document_name":"纪要","mode":"replace","markdown":"# 周会\n\n讨论进度"}
```

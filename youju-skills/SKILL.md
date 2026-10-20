---
name: youjulab
description: Read and write youjulab spreadsheets, documents, and mind maps through mcp-server. Use MCP when available, otherwise the REST routes on the same process.
---

# youjulab 表格、文档与导图

公告 PDF 的结构提取参见同目录 announcement-pdf/SKILL.md：调用 generate_pdf_outline(document_id)，仅处理有文本层的 PDF；模型只选择规则，全文解析在本地完成。

用同一 mcp-server 进程读写办公表格、文档和思维导图。能连接 MCP 时用 MCP；只能发 HTTP 时用同路径的 REST。不要调用财务 `/mcp`（指标、报表、PDF 解析），也不要走 `/api/office` 选区 Agent。

## 连接

每个请求带：`Authorization: Bearer __ACCESS_TOKEN__`，`Content-Type: application/json`。

令牌只用来找到登录邮箱账号。项目、表格和文档挂在该账号下，不挂在令牌上。重新生成令牌后资产仍在，必须用新下载的 skill 替换旧的。

| 能力 | MCP | REST |
| --- | --- | --- |
| 表格 | `http://127.0.0.1:8000/mcp/sheets` | `POST http://127.0.0.1:8000/api/spreadsheets/{create,read,write,merge,unmerge,background,border,font}` |
| 文档 | `http://127.0.0.1:8000/mcp/documents` | `POST http://127.0.0.1:8000/api/documents/{create,read,write,format}` |
| 导图 | `http://127.0.0.1:8000/mcp/mindmaps` | `POST http://127.0.0.1:8000/api/mindmaps/{create,read,write,add-node,update-node,delete-node,move-node}` |

stdio 拉起的财务入口不含这些工具。财务地址 `http://127.0.0.1:8000/mcp` 不要混用。

输入一律用名称，不要用内部 ID。

## 表格

公共参数：`project_name`、`table_name`、`sheet_name`。范围：`start_cell`（默认 `A1`）、`end_cell`。

| 函数 | REST | 说明 |
| --- | --- | --- |
| create_spreadsheet | POST /api/spreadsheets/create | 创建缺失的项目 / 表格 / 工作表。同名 sheet 已存在则失败。 |
| read_spreadsheet | POST /api/spreadsheets/read | 读取单元格。不自动创建。可传 `start_cell` / `end_cell`。返回 `values`、`sheet_names`、`revision`。公式以 `=` 开头。 |
| write_spreadsheet | POST /api/spreadsheets/write | 从 `start_cell` 写入二维 `values`。名称不存在会先创建。写入会保留已有样式。 |
| merge_cells | POST /api/spreadsheets/merge | 合并 `start_cell` 到 `end_cell`。与已有合并重叠则失败。 |
| unmerge_cells | POST /api/spreadsheets/unmerge | 取消与该区域重叠的合并。 |
| set_cell_background | POST /api/spreadsheets/background | `color` 为 `#RRGGBB` 或 red/yellow/blue/green/gray/black/white；空或 none 清除。省略 `end_cell` 只改起点。 |
| set_cell_border | POST /api/spreadsheets/border | `style`：none/thin/medium/thick/dashed/dotted/double。`sides`：all/outside/inside/top/bottom/left/right。`color` 默认 `#000000`。 |
| set_cell_font | POST /api/spreadsheets/font | 只改提供的项：font_family、font_size、bold、italic、underline、strikethrough、color、align（left/center/right）、vertical_align（top/middle/bottom）。 |

一次最多约 10000 个单元格。更大的表用 `start_cell` / `end_cell` 分块读，或分多次写入。不要把 HTML 或截图当表格内容；本接口只读写数据和样式，渲染由办公工作区完成。

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

只能发 HTTP 时，把上面的函数名对应到 REST：create → `/create`，read → `/read`，write → `/write`，merge → `/merge`，unmerge → `/unmerge`，background → `/background`，border → `/border`，font → `/font`。

```
POST http://127.0.0.1:8000/api/spreadsheets/write
Authorization: Bearer __ACCESS_TOKEN__
Content-Type: application/json

{"project_name":"年度预算","table_name":"销售表","sheet_name":"Q1","start_cell":"A1","values":[["月份","金额"],["1月",100]]}
```

MCP 等价调用：

```
write_spreadsheet(project_name="年度预算", table_name="销售表", sheet_name="Q1", start_cell="A1", values=[["月份","金额"],["1月",100]])
merge_cells(project_name="年度预算", table_name="销售表", sheet_name="Q1", start_cell="A1", end_cell="B1")
set_cell_font(project_name="年度预算", table_name="销售表", sheet_name="Q1", start_cell="A1", bold=true, font_size=14, align="center")
```

## 文档

文档不要用表格函数。参数是 `project_name` 和 `document_name`。鉴权仍用本文件的 Bearer token。

| 函数 | REST | 说明 |
| --- | --- | --- |
| create_document | POST /api/documents/create | 创建项目（若缺）和文档。同名文档已存在则失败。 |
| read_document | POST /api/documents/read | 返回 markdown 和 blocks（index、type、text、marks）。不自动创建。 |
| write_document | POST /api/documents/write | markdown 加 mode：replace 整篇替换，append 追加到末尾，prepend 插到最上方，insert 插到 block_index 之前，update 替换该块。insert 和 update 需要 block_index。名称不存在会创建。`---` 是分割线。 |
| format_document | POST /api/documents/format | block_index 必填。block_type：paragraph、h1、h2、h3、bullet、ordered、blockquote、code。bold、italic、strike、code 只改提供的项，作用在该块全部文字上。 |

正文用 Markdown：`#` / `##` / `###` 标题，`---` 分割线，`-` 与 `1.` 列表，编号下的 `○ ` 是空心圆，同一行里从 `🔗` 起到原文链接会缩小一号，`>` 引用，围栏代码块，`**加粗**`、`*斜体*`、`~~删除线~~`、`` `行内代码` ``。文档没有单元格那种字体颜色和边框。

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
      "description": "用 Markdown 修改文档。mode 为 replace、append、prepend、insert 或 update。prepend 把新内容放到最上方。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "document_name": { "type": "string" },
          "markdown": { "type": "string" },
          "mode": { "type": "string", "enum": ["replace", "append", "prepend", "insert", "update"] },
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

## 导图

导图参数使用 `project_name` 和 `mindmap_name`，节点通过稳定的 `node_id` 定位。先用 `read_mindmap` 获得当前 ID、父子关系和 revision；不要用节点标题猜 ID。节点变更会保留 Mind Elixir 的 `mind-elixir-v1` 格式，工作区重新打开后直接显示。

| 函数 | REST | 说明 |
| --- | --- | --- |
| create_mindmap | POST /api/mindmaps/create | 创建项目（若缺）和导图。`root_topic` 可选，默认使用导图名；同名导图已存在则失败。 |
| read_mindmap | POST /api/mindmaps/read | 返回嵌套 `root` 和扁平 `nodes`；每个扁平节点含 `id`、`parent_id`、`path`、`child_count`。不自动创建。 |
| write_mindmap | POST /api/mindmaps/write | 用嵌套 `root` 整树替换。缺失项目或导图会创建。每个节点必须有唯一 `id`、非空 `topic` 和 `children` 数组。 |
| add_mindmap_node | POST /api/mindmaps/add-node | 在 `parent_id` 下新增节点。可传 `note`、字符串 `tags`、`hyperlink`、自定义 `node_id`；省略 ID 时自动生成。 |
| update_mindmap_node | POST /api/mindmaps/update-node | 按 `node_id` 修改提供的字段。`note`、`tags`、`hyperlink` 传空值可清除；`topic` 不可为空。 |
| delete_mindmap_node | POST /api/mindmaps/delete-node | 删除节点和全部后代，返回 `deleted_count`。不能删除根节点。 |
| move_mindmap_node | POST /api/mindmaps/move-node | 移到 `new_parent_id` 下；`index=-1` 追加，非负数表示插入位置。拒绝循环父子关系和移动根节点。 |

需要一次生成完整导图时优先调用一次 `write_mindmap`；增量修改现有导图时使用节点接口，避免覆盖用户同时添加的其它分支。发生 revision 冲突时服务会自动重读并重试一次。

```json
[
  {
    "type": "function",
    "function": {
      "name": "create_mindmap",
      "description": "按项目名和导图名创建思维导图，可指定中心主题。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "mindmap_name": { "type": "string" },
          "root_topic": { "type": "string" }
        },
        "required": ["project_name", "mindmap_name"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "read_mindmap",
      "description": "读取完整导图树和包含节点路径、父节点 ID 的扁平列表。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "mindmap_name": { "type": "string" }
        },
        "required": ["project_name", "mindmap_name"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "write_mindmap",
      "description": "用嵌套 root 整树替换导图；不存在时创建。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "mindmap_name": { "type": "string" },
          "root": { "$ref": "#/$defs/mindmapNode" }
        },
        "required": ["project_name", "mindmap_name", "root"],
        "$defs": {
          "mindmapNode": {
            "type": "object",
            "properties": {
              "id": { "type": "string" },
              "topic": { "type": "string" },
              "children": { "type": "array", "items": { "$ref": "#/$defs/mindmapNode" } },
              "note": { "type": "string" },
              "tags": { "type": "array", "items": { "type": "string" } },
              "hyperLink": { "type": "string" }
            },
            "required": ["id", "topic", "children"]
          }
        }
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "add_mindmap_node",
      "description": "在指定父节点下新增导图节点。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "mindmap_name": { "type": "string" },
          "parent_id": { "type": "string" },
          "topic": { "type": "string" },
          "note": { "type": "string" },
          "tags": { "type": "array", "items": { "type": "string" } },
          "hyperlink": { "type": "string" },
          "node_id": { "type": "string" }
        },
        "required": ["project_name", "mindmap_name", "parent_id", "topic"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "update_mindmap_node",
      "description": "按节点 ID 更新主题、备注、标签或超链接，只修改提供的字段。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "mindmap_name": { "type": "string" },
          "node_id": { "type": "string" },
          "topic": { "type": "string" },
          "note": { "type": "string" },
          "tags": { "type": "array", "items": { "type": "string" } },
          "hyperlink": { "type": "string" }
        },
        "required": ["project_name", "mindmap_name", "node_id"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "delete_mindmap_node",
      "description": "删除指定节点及其全部后代，根节点不可删除。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "mindmap_name": { "type": "string" },
          "node_id": { "type": "string" }
        },
        "required": ["project_name", "mindmap_name", "node_id"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "move_mindmap_node",
      "description": "把节点移动到新父节点下，可指定从 0 开始的插入位置。",
      "parameters": {
        "type": "object",
        "properties": {
          "project_name": { "type": "string" },
          "mindmap_name": { "type": "string" },
          "node_id": { "type": "string" },
          "new_parent_id": { "type": "string" },
          "index": { "type": "integer", "default": -1 }
        },
        "required": ["project_name", "mindmap_name", "node_id", "new_parent_id"]
      }
    }
  }
]
```

MCP 示例：

```
create_mindmap(project_name="公司研究", mindmap_name="腾讯投资逻辑", root_topic="腾讯控股")
add_mindmap_node(project_name="公司研究", mindmap_name="腾讯投资逻辑", parent_id="root", topic="核心业务", node_id="business")
add_mindmap_node(project_name="公司研究", mindmap_name="腾讯投资逻辑", parent_id="business", topic="游戏", tags=["收入", "竞争优势"])
```

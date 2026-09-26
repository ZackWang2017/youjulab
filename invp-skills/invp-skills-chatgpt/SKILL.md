---
name: invp-skills-chatgpt
description: Read, write, and format Invp office spreadsheets and documents from ChatGPT via mcp-server /mcp/sheets and /mcp/documents.
---

# Invp 表格读写（ChatGPT）

用 **同一 mcp-server 进程** 的表格入口创建、读取、写入、合并单元格、设置背景色 / 边框 / 字体。不要调用财务 `/mcp`（指标、报表结构、PDF 解析/渲染），也不要走 `/api/office` 选区 Agent。

## 连接

- 表格 MCP：`http://127.0.0.1:8000/mcp/sheets`
- 表格 REST：`POST /api/spreadsheets/create`、`/read`、`/write`、`/merge`、`/unmerge`、`/background`、`/border`、`/font`
- 鉴权：`Authorization: Bearer __ACCESS_TOKEN__`
- 令牌只用来找到你的登录邮箱账号。项目、表格挂在该账号下，不挂在令牌上。重新生成令牌后资产仍在，必须用新 skill 替换旧的。

财务 MCP 仍是 `http://127.0.0.1:8000/mcp`，不要混用。

## 工具

输入一律用名称，不要用内部 ID。

| 参数 | 含义 |
| --- | --- |
| project_name | 项目名 |
| table_name | 表名（工作簿） |
| sheet_name | sheet 名（工作表标签） |
| values | 仅写入：二维数组 |
| start_cell | 起点，默认 `A1` |
| end_cell | 右下角；读取时可省略（读到已用区域末尾）；合并时必填 |

### create_spreadsheet

创建缺失的项目 / 表格 / 工作表。同名 sheet 已存在则失败。

### read_spreadsheet

读取指定 sheet 的单元格。不存在则报错，不会自动创建。`values` 是二维数组；公式以 `=` 开头。返回里还有 `sheet_names`、`revision`。

### write_spreadsheet

从 `start_cell` 合并写入。项目 / 表格 / sheet 不存在时会创建。写入会保留已有样式。

### merge_cells / unmerge_cells

`merge_cells` 把 `start_cell` 到 `end_cell` 合成一个单元格。与已有合并重叠则失败。`unmerge_cells` 取消与该区域重叠的合并。

### set_cell_background

给 `start_cell:end_cell` 设背景色。`color` 为 `#RRGGBB` 或 `red`/`yellow`/`blue`/`green`/`gray`/`black`/`white`；空或 `none` 表示清除。省略 `end_cell` 则只改起点。

### set_cell_border

`style`：`none` / `thin` / `medium` / `thick` / `dashed` / `dotted` / `double`。`sides`：`all` / `outside` / `inside` / `top` / `bottom` / `left` / `right`。`color` 默认 `#000000`。

### set_cell_font

只改提供的项：`font_family`、`font_size`、`bold`、`italic`、`underline`、`strikethrough`、`color`、`align`（left/center/right）、`vertical_align`（top/middle/bottom）。

## 示例

```
read_spreadsheet(project_name="年度预算", table_name="销售表", sheet_name="Q1")
write_spreadsheet(
  project_name="年度预算",
  table_name="销售表",
  sheet_name="Q1",
  start_cell="A1",
  values=[["月份", "金额"], ["1月", 100]]
)
merge_cells(project_name="年度预算", table_name="销售表", sheet_name="Q1", start_cell="A1", end_cell="B1")
set_cell_background(project_name="年度预算", table_name="销售表", sheet_name="Q1", start_cell="A1", end_cell="B1", color="#FFFF00")
set_cell_border(project_name="年度预算", table_name="销售表", sheet_name="Q1", start_cell="A1", end_cell="B2", style="thin", sides="outside")
set_cell_font(project_name="年度预算", table_name="销售表", sheet_name="Q1", start_cell="A1", end_cell="B1", bold=true, font_size=14, align="center")
```

REST 读取：

```
POST /api/spreadsheets/read
Authorization: Bearer __ACCESS_TOKEN__
{"project_name":"年度预算","table_name":"销售表","sheet_name":"Q1","start_cell":"A1"}
```

## 不要做的事

- 不要调用 `get_indicators`、`get_statements`、`parse_pdf`、`run_office_agent`
- 不要把 HTML 或截图当表格内容；本接口只读写数据和样式，渲染由办公工作区完成

## 文档

文档和表格分开。用项目名 `project_name` 和文档名 `document_name`，不要用内部 ID。

- 文档 MCP：`http://127.0.0.1:8000/mcp/documents`
- 文档 REST：`POST /api/documents/create`、`/read`、`/write`、`/format`
- 鉴权与表格相同：`Authorization: Bearer __ACCESS_TOKEN__`

| 函数 | 说明 |
| --- | --- |
| create_document | 创建缺失的项目和文档。同名文档已存在则失败。 |
| read_document | 读取全文。返回 `markdown` 和 `blocks`（index、type、text、marks）。不自动创建。 |
| write_document | `markdown` 写入。`mode`：replace 整篇替换，append 追加，insert 插到 `block_index` 前，update 替换该块。名称不存在会先创建。 |
| format_document | 改某一个块。`block_type`：paragraph、h1、h2、h3、bullet、ordered、blockquote、code。`bold` / `italic` / `strike` / `code` 只改提供的项，作用在该块全部文字上。 |

正文用 Markdown：`#` 标题、`-` / `1.` 列表、`>` 引用、代码块，以及 `**加粗**`、`*斜体*`、`~~删除线~~`、`` `行内代码` ``。办公文档没有单元格字体颜色和边框。

```
read_document(project_name="年度预算", document_name="纪要")
write_document(project_name="年度预算", document_name="纪要", mode="replace", markdown="# 周会\n\n讨论进度")
format_document(project_name="年度预算", document_name="纪要", block_index=0, block_type="h2", bold=true)
```

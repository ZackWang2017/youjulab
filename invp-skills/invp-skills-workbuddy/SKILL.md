---
name: invp-skills-workbuddy
description: Read, write, and format Invp office spreadsheets and documents from WorkBuddy via mcp-server.
---

# Invp 表格读写（WorkBuddy）

WorkBuddy 连接 **mcp-server 同一进程** 的表格入口。只使用本 skill 列出的工具；不要走财务 MCP，也不要做表格渲染。

## 连接

- 表格 MCP：`http://127.0.0.1:8000/mcp/sheets`
- 表格 REST：`POST /api/spreadsheets/create`、`/read`、`/write`、`/merge`、`/unmerge`、`/background`、`/border`、`/font`
- Header：`Authorization: Bearer __ACCESS_TOKEN__`
- 令牌只用来找到你的登录邮箱账号。项目和表格挂在该账号下，不挂在令牌上。重新生成后资产仍在，请用新下载的 skill 替换。

stdio 拉起的是财务 finresearch，不含表格工具。表格请用 HTTP `/mcp/sheets` 或 REST。

## 工具参数

1. **项目名** `project_name`
2. **表名** `table_name`
3. **sheet名** `sheet_name`

`create_spreadsheet`：创建缺失的项目、表格和工作表；同名 sheet 已存在则失败。

`read_spreadsheet`：按名称读取。默认从 `start_cell`（A1）读到已用区域；可传 `end_cell` 限定范围。缺失名称会报错，不会创建。

`write_spreadsheet`：写入二维 `values`，起点 `start_cell`（默认 A1）。名称不存在会先创建再写。写入会保留已有样式。

`merge_cells`：合并 `start_cell` 到 `end_cell`。与已有合并重叠则失败。

`unmerge_cells`：取消与该区域重叠的合并。

`set_cell_background`：`color` 为 `#RRGGBB` 或颜色名；空或 `none` 清除。省略 `end_cell` 只改起点。

`set_cell_border`：`style` 为 none/thin/medium/thick/dashed/dotted/double；`sides` 为 all/outside/inside/top/bottom/left/right。

`set_cell_font`：只改提供的项（`font_family`、`font_size`、`bold`、`italic`、`underline`、`strikethrough`、`color`、`align`、`vertical_align`）。

## 文档

文档走另一组入口，不要用表格工具改文档。

- 文档 MCP：`http://127.0.0.1:8000/mcp/documents`
- 文档 REST：`POST /api/documents/create`、`/read`、`/write`、`/format`
- Header 仍是 `Authorization: Bearer __ACCESS_TOKEN__`
- 参数是 **项目名** `project_name` 和 **文档名** `document_name`

`create_document`：创建缺失的项目和文档。同名文档已存在则失败。

`read_document`：返回 `markdown` 和 `blocks`。每个块有 `index`、`type`、`text`、`marks`。缺失不会创建。

`write_document`：用 Markdown 修改正文。`mode` 为 `replace`（整篇替换）、`append`（追加）、`insert`（插到 `block_index` 之前）、`update`（替换该块）。名称不存在会先创建。

`format_document`：改 `block_index` 指定的块。`block_type` 取 `paragraph`、`h1`、`h2`、`h3`、`bullet`、`ordered`、`blockquote`、`code`。`bold`、`italic`、`strike`、`code` 为布尔值，只改传入的项，并作用在该块全部文字上。

Markdown 写法：`#` / `##` / `###` 标题，`-` 与 `1.` 列表，`>` 引用，围栏代码块，`**加粗**`、`*斜体*`、`~~删除线~~`、`` `行内代码` ``。文档没有单元格那种字体颜色和边框。

```
write_document(project_name="年度预算", document_name="纪要", mode="replace", markdown="# 周会\n\n讨论进度")
format_document(project_name="年度预算", document_name="纪要", block_index=0, block_type="h2", bold=true)
```

```
write_spreadsheet(
  project_name="<项目名>",
  table_name="<表名>",
  sheet_name="<sheet名>",
  start_cell="A1",
  values=[["列1", "列2"], ["v1", 2]]
)
merge_cells(project_name="<项目名>", table_name="<表名>", sheet_name="<sheet名>", start_cell="A1", end_cell="B1")
set_cell_background(project_name="<项目名>", table_name="<表名>", sheet_name="<sheet名>", start_cell="A1", end_cell="B1", color="yellow")
set_cell_border(project_name="<项目名>", table_name="<表名>", sheet_name="<sheet名>", start_cell="A1", end_cell="B2", style="thin", sides="outside")
set_cell_font(project_name="<项目名>", table_name="<表名>", sheet_name="<sheet名>", start_cell="A1", bold=true, font_size=14, align="center")
```

读取成功返回 `values`、`sheet_names`、`revision`。把名称原样告诉用户即可。

## 边界

- 不要调用 8000 端口上的 `/mcp` 财务工具或 `/api/financial/*`
- 不要调用 `/api/office` 选区解释
- 一次最多约 10000 个单元格；更大的表用 `start_cell` / `end_cell` 分块读，或分多次写入

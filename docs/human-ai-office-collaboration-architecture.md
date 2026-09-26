# 人与 AI 协同 Office 工具架构草案

目标：基于开源表格、富文本文档、PDF 阅读能力，捕获人类在单元格、富文本、PDF 词语/段落上的选择与编辑上下文，并把这些上下文稳定地交给自定义 skills 和 agent 执行，再把 AI 的建议、批注、改写或结构化结果回写到同一工作界面。

## 1. 开源工具栈选择

### 推荐主栈

| 层 | 推荐 | 用途 | 选择理由 |
| --- | --- | --- | --- |
| 表格与文档运行时 | Univer | 表格、富文本文档、后续可扩展 slides/canvas | Apache-2.0；表格、文档、演示采用同一 SDK 架构；有插件系统、Facade API、浏览器和 Node headless 运行时；适合把 selection/range/document model 暴露给 agent。 |
| PDF 阅读器 | PDF.js | PDF 渲染、分页、搜索、文本选择 | Apache-2.0；Mozilla 维护；成熟稳定；足够支撑“选词、选段、定位页码、提取文本坐标”的第一版。 |
| 富文本底层备选 | ProseMirror/Tiptap + Yjs | 如果 Univer Docs 不满足复杂编辑器定制，再单独承接富文本 | ProseMirror 模型成熟，Yjs 有官方绑定，适合协同编辑、评论、局部 selection、结构化 patch。 |
| 协同同步 | Yjs | 多人协同、agent 与人的并发编辑、presence | 可承接表格 selection、富文本 cursor、PDF 标注层的共享状态。Univer 自带 Pro 协同能力时，可先以 Yjs 做开源自研同步层。 |
| 文件解析/转换 | LibreOffice headless、SheetJS、Mammoth、PyMuPDF | xlsx/docx/pdf 的导入、导出、文本抽取 | PDF 解析当前项目已有 PyMuPDF；表格和文档转换可按需要渐进接入。 |
| Agent/MCP 服务 | 现有 `mcp-server` FastMCP | skills/agent 调用统一入口 | 当前项目已经有 `mcp-server/server.py`，支持 stdio 和 streamable-http，复用它新增 Office 协作工具即可。 |

### 为什么不优先选 ONLYOFFICE / Collabora

ONLYOFFICE 和 Collabora 更像完整办公套件，适合“嵌入一个现成 Office 编辑器”。但你的目标是“完全融合的人与 AI 协同”：需要精细捕获 selection、cell/range、富文本节点、PDF 词坐标，并把 AI 动作变成可审阅 patch。完整办公套件的内部模型和事件通常更重，二次深度集成成本高。

它们可作为后续“高保真 Office 格式编辑/导出”备选，不建议作为第一版 agent-native runtime。

### PDF 取舍

第一版用 PDF.js 实现阅读、搜索、文本选择、页码定位和标注叠加层。PDF.js 本身不提供完整内容编辑、签名、复杂批注持久化等高级能力。如果后续需要“AI 直接改 PDF 内容、填表、涂黑、签章、企业级批注同步”，可以保留一个 PDF provider 接口，未来替换到 Nutrient 或类似商业 SDK。

## 2. Agent 和 Skills 目录框架

建议新增如下目录，保留当前 `mcp-server` 作为工具服务根：

```text
.
├── apps/
│   └── office-copilot-web/
│       ├── src/
│       │   ├── editors/
│       │   │   ├── sheet/              # Univer Sheet 封装
│       │   │   ├── document/           # Univer Docs 或 ProseMirror 封装
│       │   │   └── pdf/                # PDF.js viewer + 选择层
│       │   ├── ai-panel/               # 人机协同面板、命令输入、结果审阅
│       │   ├── context/                # selection/change/context 采集
│       │   ├── patches/                # AI 修改预览、diff、应用/撤销
│       │   └── mcp-client/             # 调用 mcp-server 的浏览器/后端代理
│       └── package.json
├── agents/
│   ├── office-copilot.agent.yaml        # 总控 agent：根据上下文路由 skills
│   ├── spreadsheet-analyst.agent.yaml   # 表格分析/公式/清洗
│   ├── document-editor.agent.yaml       # 文档改写/摘要/结构化
│   └── pdf-reader.agent.yaml            # PDF 伴读/引用/抽取
├── skills/
│   ├── spreadsheet/
│   │   ├── skill.yaml
│   │   ├── prompts/
│   │   │   ├── analyze-range.md
│   │   │   ├── generate-formula.md
│   │   │   └── clean-table.md
│   │   └── references/
│   │       └── spreadsheet-context-schema.md
│   ├── document/
│   │   ├── skill.yaml
│   │   ├── prompts/
│   │   │   ├── rewrite-selection.md
│   │   │   ├── summarize-section.md
│   │   │   └── extract-claims.md
│   │   └── references/
│   │       └── richtext-patch-schema.md
│   ├── pdf/
│   │   ├── skill.yaml
│   │   ├── prompts/
│   │   │   ├── explain-selection.md
│   │   │   ├── cite-page.md
│   │   │   └── extract-table.md
│   │   └── references/
│   │       └── pdf-selection-schema.md
│   └── common/
│       ├── context-contract.md
│       ├── patch-contract.md
│       └── citation-contract.md
└── mcp-server/
    ├── server.py                        # 继续作为 MCP 入口
    ├── office_context.py                # 新增：工作区/selection/context 工具
    ├── office_patch.py                  # 新增：AI patch 校验、应用、回滚
    ├── office_files.py                  # 新增：xlsx/docx/pdf 文件读写与转换
    └── pdf_report.py                    # 现有：可扩展 PDF 文本/图片/坐标工具
```

### 核心数据契约

所有编辑器都把“用户当前关注点”统一成 `OfficeContext`：

```json
{
  "workspace_id": "local-workspace",
  "document_id": "file-or-tab-id",
  "surface": "sheet | document | pdf",
  "selection": {
    "kind": "cell | range | rich_text | pdf_text | pdf_area",
    "address": "Sheet1!A1:C20",
    "page": 12,
    "text": "用户选中的文字",
    "bounding_boxes": []
  },
  "surrounding_context": {
    "before": "",
    "after": "",
    "headers": [],
    "nearby_cells": []
  },
  "user_intent": "explain | rewrite | analyze | extract | formula | custom",
  "provenance": {
    "source_file": "",
    "revision": "",
    "timestamp": ""
  }
}
```

AI 输出统一成 `OfficePatch`，永远先审阅再应用：

```json
{
  "patch_id": "uuid",
  "surface": "sheet | document | pdf",
  "operations": [
    {
      "op": "replace_text | insert_text | update_cells | add_comment | add_annotation",
      "target": {},
      "value": {},
      "reason": ""
    }
  ],
  "citations": [],
  "validation": {
    "checks": [],
    "warnings": []
  }
}
```

### Agent 路由

`office-copilot.agent.yaml` 只做编排：

1. 读取当前 `OfficeContext`。
2. 根据 `surface` 和 `user_intent` 选择 skill。
3. 调用 `mcp-server` 获取精确上下文，例如单元格值、富文本节点、PDF 页文本。
4. 生成 `OfficePatch` 或只读回答。
5. 调用 `validate_patch`。
6. 在前端展示 diff/批注，由用户确认后调用 `apply_patch`。

专业 agent 则聚焦领域任务：

| Agent | 负责 |
| --- | --- |
| `spreadsheet-analyst` | range 分析、公式生成、表格清洗、异常检测、字段映射 |
| `document-editor` | 局部改写、语气调整、摘要、引用整理、结构化提纲 |
| `pdf-reader` | PDF 选词解释、页码引用、表格抽取、研报/财报伴读 |

## 3. 复用现有 `mcp-server`

当前 `mcp-server/server.py` 已经具备三个关键基础：

1. `FastMCP("finresearch")` 作为统一 MCP server。
2. 同时支持 `stdio` 和 `streamable-http`。
3. 已有 PDF 解析工具：`parse_pdf`、`pdf_page`、`pdf_text`，HTTP 侧还有 `/parse`、`/page-text`、`/page-image`、`/pdf`。

建议不要新建另一个 MCP server。直接在现有 `server.py` 中注册新的 Office 工具模块：

```python
import office_context
import office_patch
import office_files

office_context.register_tools(mcp)
office_patch.register_tools(mcp)
office_files.register_tools(mcp)
```

新增工具建议：

| MCP 工具 | 作用 |
| --- | --- |
| `get_office_context(workspace_id, document_id, selection)` | 返回统一后的 `OfficeContext`。 |
| `read_sheet_range(document_id, sheet, range)` | 读取单元格值、公式、格式、批注、表头。 |
| `write_sheet_patch(document_id, patch)` | 应用表格 patch。 |
| `read_richtext_selection(document_id, selection)` | 读取富文本选区、节点路径、段落上下文。 |
| `write_richtext_patch(document_id, patch)` | 应用文档 patch。 |
| `read_pdf_selection(path, page, boxes/text)` | 基于 PDF.js 选区或 PyMuPDF 坐标取词/段。 |
| `add_pdf_annotation(path, annotation)` | 添加高亮、批注、引用锚点。 |
| `validate_office_patch(patch)` | 校验 patch 是否可应用、是否越界、是否破坏结构。 |
| `preview_office_patch(patch)` | 返回前端 diff/预览数据。 |

第一阶段里，MCP server 只做“确定性工具层”：读取、解析、定位、校验、应用 patch。LLM/agent 负责理解和规划，但不直接猜测单元格范围、PDF 页码、文档节点路径。

## 分阶段落地

### MVP

1. Web 前端集成 Univer Sheet + PDF.js。
2. 捕获表格 range selection、PDF text selection。
3. `mcp-server` 新增 `office_context.py` 和 `office_patch.py`。
4. 先实现只读 skills：解释单元格、解释 PDF 选词、总结选中文档片段。
5. 再实现可写 patch：表格填充、富文本替换、PDF 高亮批注。

### 第二阶段

1. 引入 Univer Docs 或 ProseMirror/Tiptap 文档编辑。
2. 引入 Yjs presence，同步人和 agent 的 cursor、selection、patch 状态。
3. 加入 diff 审阅、撤销、版本历史。
4. 扩展 `skills/common` 的引用、证据、校验规范。

### 第三阶段

1. 加入文件导入导出：xlsx/docx/pdf。
2. 加入多 agent 并行：表格分析 agent、PDF 抽取 agent、文档编辑 agent。
3. 建立回归测试：同一个 selection 输入必须产生稳定 context，patch 必须可预览、可回滚。

## 关键原则

1. selection 是第一等对象：人选中哪里，agent 就必须知道哪里、周边是什么、证据来自哪里。
2. patch 是第一等对象：AI 不直接写文件，先生成结构化 patch，用户审阅后应用。
3. MCP 是确定性边界：解析、坐标、range、文件读写、校验都放进工具层。
4. skills 是任务知识：提示词、上下文契约、输出格式和校验规则放在 skills 中，方便独立演进。
5. agent 是编排器：不要把复杂文件操作写进 prompt，prompt 只决定“该调用哪个工具、如何解释结果”。
# MVP Implementation Note

The implemented local MVP uses Univer Sheets, Tiptap for rich text, and PDF.js.
Skills use SKILL.md entrypoints. The existing mcp-server hosts office context,
patch validation, and a single-turn agent runner; the browser calls its REST
adapter. Editor state is browser-owned. PDF is read-only and stays local except
for selected text and page context. See the root README.md for startup, model
configuration, current limitations, and tests. The sections below remain the
longer-term architecture plan.

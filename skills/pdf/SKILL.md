---
name: pdf
description: Explain selected PDF text with a page citation, and answer multi-turn Ask-AI questions with semantic search over the same document.
---

This skill covers two PDF read-only tasks.

## 选区解释

This is 选区解释. PDF is read-only; 生成内容 is not available.
Quote or explain only supplied text and page context. Cite the original filename
and supplied 1-based page. Selected rectangles use PDF page coordinates, not CSS
pixels. If extracted text is empty, state that OCR is needed. Do not infer unseen
tables, pages, or financial figures. replacement must be null.
Use [the shared contract](../common/contracts.md).

## 问AI

这是「问AI」。选区是提问焦点。支持多轮追问。

先看本轮问题属于哪一类，再决定检索和篇幅。文档类优先于分析类，分析类优先于概念类。

### 1. 概念题

问题在解释「是什么」「如何理解」「什么意思」，或只要概念、计量方法和会计上如何记账核算。

- 不要调用 `ensure_office_pdf_index`、`get_office_pdf_index_status`、`search_office_pdf`。
- 只查外部：`baidu_baike_search`（百科词条，不是网页搜索）、`baidu_web_search`、`google_web_search`。概念优先百科，中文事实用百度搜索，英文或国际来源用 Google。
- 回答要短：概念、怎么计算或计量、会计上如何确认与记账。不要展开案例和延伸讨论。

### 2. 分析题

问题里出现「请分析」「为什么」「讨论一下」（含「分析一下」「讨论下」）。

- 同样不要检索本地 PDF 向量库，除非同时符合下面的文档题。
- 可以调用上述三个外部搜索。
- 回答要完整：给出推理过程、依据和结论。

### 3. 文档题

问题指向当前文件或本地库中的某份财报，例如「本文档」「本财报」「这份年报」，或写明某年、某季度的财报（如「2023年年报」「三季度财报」）。

- 先 `ensure_office_pdf_index(document_id)`，再 `search_office_pdf(document_id, query, top_k=8)`。
- 仍可按需要调用百科、百度搜索、Google。
- 若同时是分析题，在本文摘录上做完整推理；否则按问题回答并引用页码。

### 外部工具

见 [external tools](../common/external-tools.md)。`baidu_baike_search` 走百科词条接口，与 `baidu_web_search` 不是同一类查询。

工具返回 `error`、空 `results` 或 `quota.auto_closed` 时，该来源视为没有内容。

### 回答结构

用简体中文，二级标题 `##`。下面几节按这个顺序输出，**没有内容的节整节不要写**（不要写「无」占位）：

1. `## 本文搜索`：仅文档题，且本地检索有摘录时写。给出页码和归纳。没有命中则不要写这一节。
2. `## 百度百科`：仅当百科词条有标题、链接和一句摘要时写。
3. `## 百度搜索`：仅当百度网页搜索有结果时写。
4. `## Google搜索`：仅当 Google 有结果时写。
5. `## 模型搜索`：仅当百科、百度搜索、Google 都没有内容时写。用模型自身知识回答，并标明不确定之处。三者里有任一来源有内容时，不要写这一节。
6. `## 总结`：始终写。概念题保持简短；分析题和文档分析题写完整结论。引用页码或链接。不要编造未见的页、表、数字或 URL。

不要调用 `parse_pdf`。不要输出 JSON 或 `replacement`。

## 翻译

勾选「翻译并解释」后才翻译，不要自行判断选区是不是英文。未勾选时按上面的问AI处理。

- 术语表给出的译法必须使用。项目术语优先于内置术语。
- 同页前后文只用于指代和科目口径，不要把上下文译进正文，也不要用上下文里的数字改选区数字。
- 金额、百分比、日期与选区一致。EBITDA、IFRS、ASC 保留英文。
- 不调用百科、百度搜索、Google 或 `search_office_pdf`。
- 回答顺序：`## 译文`、`## 解释`、`## 术语`、`## 校对`。校对不通过时不要写解释。

Office UI stores the Q&A into a PDF text annotation titled `office-ai-ask`（badge「问AI」）.
History in the annotation is append-only.

# External tools (include)

Shared convention for any skill that needs live web facts. Topic skills include this
fragment only — do not put surface-specific answer layouts here.

## MCP

- Endpoint: `http://127.0.0.1:8000/mcp/external-tools` (or your deployed host)
- Auth: static `MCP_ACCESS_TOKEN` (Bearer), same as finresearch
- Not finresearch `/mcp` and not sheets `/mcp/sheets`

## When to use

Need current web facts beyond supplied local context. Search first, then answer.
Cite title + link. If a tool returns `error`, empty `results`, or `quota.auto_closed`,
treat that engine as unavailable for this turn — do not invent.

Prefer Baidu for Chinese queries; `baidu_baike_search` for definitions / encyclopedia;
Google for English / international sources.

**Language:** Default answer language is **简体中文**. If a cited snippet, title, or
source page is in 繁体中文, paraphrase and write the answer in 简体中文 (keep
proper nouns / tickers as in the source when needed). Do not paste long Traditional
Chinese blocks into the reply.

## Tools

- `baidu_web_search(query, num=5, topic="")`
- `baidu_baike_search(query, num=5)` — 百科词条接口（lemmaTitle），不是百度网页搜索
- `google_web_search(query, num=5, topic="")`
- `search_quota_status()` — switch and today's usage before calling

Return shape: `{ engine, query, topic?, results:[{title,link,snippet}], quota }` or
`{ error, results: [], quota }`. `quota` includes `enabled`, `used_today`,
`daily_limit`, `auto_closed`. When switch off or auto-closed, do not retry that engine.

### Optional `topic`（仅 baidu_web_search / google_web_search）

| topic | Meaning |
|-------|---------|
| `""` | Broad (Google: whole CSE site list; Baidu: open web) |
| `finance` / `accounting` / `encyclopedia` | Site subset from `SEARCH_SITES_*` |

## Rules

1. Call at most a few searches; refine `query` rather than dumping raw results.
2. Quote links in Markdown when citing.
3. Do not scrape SERP HTML. Reading one result page is `read_web_page` on `/mcp/local-tools`, a separate MCP.
4. Answer layout (section headings, PDF-only structure, etc.) belongs in the
   calling topic skill, not in this common fragment.

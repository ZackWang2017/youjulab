---
name: document-digest
description: Daily digest — organize fetched RSS/URL items into Markdown for a TipTap document.
---

This skill covers the office 「定时任务 / 每日资讯」 background job.

## Task

You receive JSON with:
- `title`: e.g. `每日资讯（2026-09-17 08:00）`
- `topics`: array of `{ name, items: [{ title, url, summary, source_label }] }`

Organize only the supplied items. Do not invent headlines, quotes, or URLs that were not provided.
If a topic has an empty `items` list, write one short line that no usable items were fetched.

## Output

Return Markdown only (not JSON):

1. Start with `# {title}` (or keep the given title as H1).
2. One `##` section per topic name.
3. Under each topic, bullet key points; include source links as Markdown links when `url` is present.
4. Prefer concise Chinese (or the language of the items). No document-edit proposals, no `replacement` field.

If the job later needs live web lookup beyond supplied items, use
[external tools](../common/external-tools.md).

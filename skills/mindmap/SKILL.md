---
name: mindmap
description: Explain or rewrite the selected mind-map node topic.
---

This is 选区解释 for a mind map node. The selection includes `id`, `text`
(node topic), `path` (root → node), `parent_id`, and `is_root`.

Explain the node in context of its path and siblings in surrounding.
Rewrite the topic only if the user asks. Return plain text for a rewrite
(the node topic only — no Markdown). For explanation or summary return
null replacement.

Do not invent sibling nodes or change the tree structure on explain.
Use [the shared contract](../common/contracts.md).
When you need live web facts, include [external tools](../common/external-tools.md).

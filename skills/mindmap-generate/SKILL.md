---
name: mindmap-generate
description: Insert a new child node under the selected mind-map node.
---

This is 生成内容 for a mind map. The caret is the currently selected node
(`id`, `text`, `path`, `parent_id`, `is_root`).

Produce one short child-node topic (plain text, no Markdown) that fits the
intent and parent path. Return it as `replacement` string. The editor will
insert it as a child of the selected node.

Do not return multiple topics unless the user explicitly asks for a list;
if listing, still return a single concise topic for the first child and
mention other ideas only in `answer`.
Use [the shared contract](../common/contracts.md).
When you need live web facts, include [external tools](../common/external-tools.md).

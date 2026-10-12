---
name: mindmap-generate
description: Propose reviewed structural changes to the current mind-map tree.
---

This is 生成内容 for a mind map. The caret is the currently selected node
(`id`, `text`, `path`, `parent_id`, `is_root`).

Use the current node tree in surrounding to turn pasted material into a clear
hierarchy. Return `replacement: null` plus `mindmap_ops` made of `add`,
`update`, `delete`, and `move` operations. Prefer concise node topics. Use only
ids present in `surrounding.tree` for existing nodes; each added node has a
`parent_id` and `topic`, and can specify a zero-based sibling `index`.

Do not delete the root or create cycles. The user must review and accept the
proposal before the editor applies it; never claim it is already applied.
Use [the shared contract](../common/contracts.md).
When you need live web facts, include [external tools](../common/external-tools.md).

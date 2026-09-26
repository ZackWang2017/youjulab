---
name: document-generate
description: Generate formatted rich text to insert at the document caret.
---

This is 生成内容. Insert at the caret; the selection may be empty.
Use surrounding text for tone, names, and continuity.
replacement must be Markdown that the editor converts into rich text:
use ## headings, **bold**, *italic*, and - or 1. lists. Separate paragraphs
with blank lines. Do not wrap the Markdown in a code fence. Do not return
HTML unless the user pastes HTML. answer may briefly describe the insert.
Do not claim the insert has been applied.
Use the patch rules in [the shared contract](../common/contracts.md).

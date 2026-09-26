---
name: document
description: Explain or rewrite selected rich text.
---

This is 选区解释. Use surrounding text to preserve references and tone.
Explain the selection, then rewrite only if the user asks. Do not invent facts
or change numbers during rewriting. A rewrite inside one paragraph should be plain text so existing inline marks stay.
A rewrite across paragraphs, or one that needs headings and lists, may be Markdown
(`##` headings, `**bold**`, lists). The editor converts that Markdown into rich text.
For explanation or summary return null replacement.
Use the patch rules in [the shared contract](../common/contracts.md).
When you need live web facts, include [external tools](../common/external-tools.md).

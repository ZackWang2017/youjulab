---
name: calendar
description: Explain or rewrite the selected day's work-plan Markdown.
---

This is 选区解释 for a day work plan. The selection includes `date`
(YYYY-MM-DD) and `text` (that day's Markdown content).

Explain the plan in context of its date and surrounding.
Rewrite only if the user asks. For a rewrite, return the full replacement
Markdown for that day as `replacement` (not a title/event JSON).

For explanation or summary return null replacement.

Do not invent other dates unless asked.
Use [the shared contract](../common/contracts.md).
When you need live web facts, include [external tools](../common/external-tools.md).

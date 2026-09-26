---
name: spreadsheet-generate
description: Generate spreadsheet values to insert at the active cell.
---

This is 生成内容. The snapshot marks the cursor cell (top-left of the active
range). Write new content starting there; do not require the cell to already
have a value. Surrounding cells are context, not the write target.
replacement must be a string (one cell) or a rectangular matrix that grows from
the cursor. answer is a short note about what will be inserted.
Do not overwrite distant ranges. Do not claim the insert has been applied.
Use the patch rules in [the shared contract](../common/contracts.md).

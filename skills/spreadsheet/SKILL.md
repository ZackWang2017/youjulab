---
name: spreadsheet
description: Explain or operate on a selected spreadsheet range.
---

This is 选区解释. Use the provided coordinates, values, and formulas.
Explain the selection, then operate only if the user asks. Distinguish formulas
from evaluated values; do not claim to have recalculated a workbook.
Preserve row order, matrix dimensions, units, and formulas unless asked to change
them. An edit replaces the selected range only. Return null replacement for
analysis-only answers. Never turn plain text into a formula unless requested.

When the user asks for 统计特征、均值、中位数、环比、趋势、波动 or similar
numeric characterization, follow [spreadsheet-features](../spreadsheet-features/SKILL.md)
（运行时会内联该 skill）. Prefer `surrounding.selection.stats` after「读数据」.

Use the patch rules in [the shared contract](../common/contracts.md).
When you need live web facts, include [external tools](../common/external-tools.md).

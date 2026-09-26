---
name: spreadsheet-features
description: Describe statistical and trend features of a selected spreadsheet numeric range (mean, median, MoM, trend).
---

This is **选区特征描述** for spreadsheet numeric data. Use when the user asks
about 统计特征、均值、中位数、环比、同比、波动、趋势、分布, or similar.

## Inputs

Prefer numbers already in the snapshot:

- `selection.values` — selected cell matrix
- `surrounding` JSON after「读数据」may include:
  - `selection.address` — A1 range (e.g. `B2:B13`)
  - `selection.stats` — precomputed features (`count`, `sum`, `mean`, `median`,
    `min`, `max`, `stdev`, `mom_rates`, `mom_mean`, `mom_median`, `trend`,
    `trend_slope`)
  - `structure` — whole-sheet merge-filled grid for locating headers / peers

If `selection.stats` is present, **use it as the primary evidence**. Recompute
only when stats are missing and the selection matrix is small enough to reason
about. Do not invent series that are not in the selection.

## What to report

If the question does not contain 分析 / 讨论 / 预测 / 推理 / 推测 (or 研判、展望、解读、预判、预估、成因), report only the figures the user asked for. Do not add the narrative sections below.

When the question does ask for that kind of analysis, write a concise 简体中文 feature summary covering what applies:

1. **规模**：样本数 `n`、合计、均值、中位数、最小/最大、标准差（若有）
2. **环比**：相邻点变化率；报告环比均值 / 环比中位数；点出异常跳变
3. **趋势**：上升 / 下降 / 持平（对照 `trend` / `trend_slope`）；说明是否足够长
4. **语境**：若 `structure` 能还原合并表头，用表头说明该列/行是金额、增速等

Example phrasing:

> 选区 B2:B13（n=12）：均值 1.2e8，中位 1.1e8；环比中位约 3.2%，环比均值 4.1%；
> 整体趋势缓慢上升。表头对应「营业收入」。

## Rules

- Analysis-only: `replacement` must be `null` unless the user explicitly asks
  to write results back into cells.
- Distinguish **金额水平** vs **环比/增速**（比率）；不要把比率当成金额求和。
- Percentages may appear as `0.03` or `3%` — state the convention you used.
- If `count` is 0 or too few for MoM/trend, say so; do not fabricate.
- Preserve units from headers / number formats when known; otherwise say unknown.
- Use [the shared contract](../common/contracts.md).

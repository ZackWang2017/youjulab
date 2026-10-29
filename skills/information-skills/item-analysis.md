---
name: item-analysis
description: 读取网页正文后，输出资讯摘要、业绩影响和财务指标影响。
---

你在做单条资讯的深入分析。用户消息是 JSON，含 `title`、`url`、`source_name`、`published_at`、`page`。
`page` 是已抓取的网页，含 `title`、`description`、`sections`（heading/text）。`page.error` 非空表示抓取失败。

只使用这些材料。不要编造金额、增速或占比。材料不够时写「材料不足」。

只输出一个 JSON 对象，不要 Markdown：

```json
{
  "summary": "三到六句概述事件与关键事实",
  "performance": "对收入、利润、订单或业务增长的可能影响；没有则写材料不足",
  "financial": "对毛利率、费用、现金流、资产负债等财务指标的可能影响；没有则写材料不足"
}
```

抓取失败时，`summary` 写失败原因，后两个字段写「材料不足」。

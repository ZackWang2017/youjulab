---
name: daily-review
description: 对某资讯组当日未清理资讯做深入点评，并为每条标注利好或利空。
---

你在写「今日资讯深入点评」。用户消息是 JSON，字段为 `date`、`group`、`items`。
`items` 是今天所有尚未清理的资讯（含已隐藏、已收藏），每项含 `id`、`title`、`source_name`、`published_at`、`url`、`impact`、`analysis`。

只根据这些材料写作。不要新增材料里没有的新闻，不要编造数字。必须覆盖全部条目。

只输出一个 JSON 对象，不要 Markdown：

```json
{
  "sentiment": "利好 或 利空 或 中性",
  "items": [
    {"id": 1, "sentiment": "利好 或 利空 或 中性"}
  ],
  "content": "分段点评：先总述当日未清理信息的整体倾向，再按来源或主题归纳，最后列出对业绩和财务指标的观察。使用中文，可用空行分段。"
}
```

规则：
- `sentiment` 是整体判断，必须是 利好、利空、中性 之一。
- `items` 必须包含材料里每一条的 `id`，并为每条给出利好、利空或中性。
- 条目为 0 时整体写中性，`items` 为空数组，说明今日没有可点评的资讯。
- 已有 `analysis` 的条目优先采用其业绩/财务判断。

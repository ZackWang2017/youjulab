from __future__ import annotations

import json

from assistant import iter_chat_events
from .engine import allocation_code
from .schema import validate_plan

SYSTEM = """你是量化研究 Agent。根据用户目标选择已注册工具，生成可复现研究说明与 Python 程序。
可用工具：
1. bt_allocation：仅适用于导入数据列的固定非负权重组合，不择时、不选股、不使用财务指标；支持 once/monthly/quarterly/yearly 再平衡。parameters 必须包含 weights（小数权重合计1）、rebalance、fee_bps（0至100）。该工具代码由服务端按参数生成。
2. python_research：自定义价格动量/轮动等程序，只在隔离沙箱运行。程序实现 target_weights(history, parameters)，history 为截至当日的 pandas 价格DataFrame，索引为交易日期；返回 {列名: 非负权重}，合计1。可用 pandas/numpy，不读文件、不联网、不安装包。parameters 为参数对象。
3. needs_input：缺数据、标的对应不清楚、需要未提供的基本面/历史股票池/分红等数据时，准确说明缺什么并提问。
只使用提供的数据列，不虚构行情、财务指标或实验结果。按数据口径区分价格收益与全收益。交易假设为当日收盘形成信号、下一数据日收盘执行；输入同一币种，不提供杠杆/卖空。
遇到不支持的需求不能偷偷改成固定配置。不得声称已经执行了回测。输出 JSON：
{"tool":"...","summary":"明确规则、执行假设和限制","code":"Python源码，bt_allocation可填pass","parameters":{},"assumptions":["..."]}。
修复仅改程序/格式错误，不能改投资含义。"""


async def research_events(content: dict, instruction: str):
    dataset = content.get("dataset") or {}
    rows = dataset.get("rows") or []
    profile = {"symbols": dataset.get("symbols", []), "basis": dataset.get("basis"), "source": dataset.get("source"), "rows": len(rows), "start": rows[0][0] if rows else None, "end": rows[-1][0] if rows else None}
    yield {"type": "progress", "message": "检查数据覆盖与可用工具…"}
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps({"dataset": profile, "previous_plan": content.get("plan"), "conversation": content.get("messages", [])[-6:], "request": instruction}, ensure_ascii=False)}]
    for attempt in range(3):
        yield {"type": "progress", "message": f"制定研究计划与程序（{attempt + 1}/3）…"}
        raw = ""
        async for event in iter_chat_events(messages, json_object=True, thinking=True):
            if event.get("type") == "delta":
                if event.get("part") == "reasoning":
                    yield {"type": "thinking", "text": str(event.get("text", ""))}
                else:
                    raw += str(event.get("text", ""))
                    if len(raw) > 100_000:
                        raise ValueError("模型输出过长。")
        try:
            plan = validate_plan(json.loads(raw), profile["symbols"])
            if plan["tool"] != "needs_input" and not rows:
                raise ValueError("尚未导入真实历史数据，请选择 needs_input 并要求导入 CSV。")
            if plan["tool"] == "bt_allocation":
                plan["code"] = allocation_code(plan["parameters"])
            yield {"type": "done", "plan": plan}
            return
        except (ValueError, TypeError, AttributeError) as exc:
            if attempt == 2:
                raise ValueError(f"研究程序三次校验未通过：{exc}") from exc
            messages += [{"role": "assistant", "content": raw[:50000]}, {"role": "user", "content": f"校验失败：{exc}。保持研究目标不变修复 JSON/程序；缺条件则返回 needs_input。"}]


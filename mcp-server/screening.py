"""初筛指标判定引擎 —— 确定性计算，与 LLM 无关。

读取 research-config/screening-profile.yaml（12 项初筛指标与判定标准），
对单个标的的报表数据逐项判定，输出每项数值/判定/得分 + 汇总结论。

- 调整阈值/标准/文案：改配置，不改代码。
- 新增判定类型（kind）：才需要在本文件注册（目前支持 threshold/range/tiers/asset_light/pending）。
- 所有数值做 JSON 安全清理（NaN/Inf → None）。
"""

from __future__ import annotations

import math

PERIODS = ["latest", "y1", "y2"]


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, float):
        return round(v, 4)
    return v


def _ratio(a, b):
    if b in (None, 0):
        return None
    return a / b


# ---------------------------------------------------------------------------
# 单项数值计算：输入 {period: statement}，返回该指标的数值（latest 为主）
# ---------------------------------------------------------------------------

def _compute_value(sid: str, st: dict) -> dict:
    """返回 {value, extra}；extra 为可选的补充口径（展示用）。"""
    latest = st["latest"]
    if sid == "roe":
        return {"value": _ratio(latest["net_profit"], latest["equity"]) * 100, "extra": None}
    if sid == "interest_debt_ratio":
        return {"value": _ratio(latest["interest_debt"], latest["total_assets"]) * 100, "extra": None}
    if sid == "asset_light":
        return {"value": _ratio(latest["pretax_profit"], latest["productive_assets"]) * 100, "extra": None}
    if sid == "receivables_ratio":
        receivables = latest["accounts_receivable"] + (latest["notes_receivable"] - latest["bank_acceptance"])
        return {"value": _ratio(receivables, latest["total_assets"]) * 100, "extra": None}
    if sid == "gross_margin":
        return {"value": _ratio(latest["revenue"] - latest["cogs"], latest["revenue"]) * 100, "extra": None}
    if sid == "fee_ratio":
        gp = latest["revenue"] - latest["cogs"]
        value = _ratio(latest["three_fees"], gp) * 100
        fees_to_revenue = _ratio(latest["three_fees"], latest["revenue"]) * 100
        return {"value": value, "extra": {"fee_to_revenue_pct": _clean(fees_to_revenue)}}
    if sid == "ocf_to_profit":
        value = _ratio(latest["ocf"], latest["net_profit"])
        series = {p: _clean(_ratio(st[p]["ocf"], st[p]["net_profit"])) for p in PERIODS}
        return {"value": value, "extra": {"series": series}}
    if sid == "cashflow_profile":
        return {"value": None, "extra": None}
    if sid == "current_ratio":
        return {"value": _ratio(latest["current_assets"], latest["current_liab"]), "extra": None}
    if sid == "quick_ratio":
        return {"value": _ratio(latest["current_assets"] - latest["inventory"], latest["current_liab"]), "extra": None}
    if sid == "cash_net_change":
        return {"value": latest["cash_net_change"], "extra": None}
    if sid == "cash_debt_coverage":
        numerator = latest["cash_balance"] + latest["liquid_financial_assets"]
        coverage_total = _ratio(numerator, latest["interest_debt"])
        coverage_1y = _ratio(numerator, latest["interest_debt_due_1y"])
        return {
            "value": coverage_total,
            "extra": {"coverage_vs_due_1y": _clean(coverage_1y)},
        }
    return {"value": None, "extra": None}


# ---------------------------------------------------------------------------
# 规则判定
# ---------------------------------------------------------------------------

def _pick_text(rule: dict, verdict: str, fallback: str) -> str:
    key = f"text_{verdict}"
    if key in rule and rule[key]:
        return rule[key]
    return fallback


def _apply_rule(item: dict, value, extra: dict | None) -> dict:
    rule = item.get("rule", {})
    kind = rule.get("kind", "threshold")
    note = item.get("note", "")
    if kind == "pending":
        return {"verdict": "pending", "text": note or "占位：阈值待定"}

    if value is None:
        return {"verdict": "fail", "text": "数据缺失，无法判定"}

    if kind == "threshold":
        op = rule["operator"]
        ok = {
            ">": value > rule["value"],
            ">=": value >= rule["value"],
            "<": value < rule["value"],
            "<=": value <= rule["value"],
        }[op]
        verdict = rule.get("verdict_if_true", "pass") if ok else rule.get("verdict_if_false", "fail")
        return {"verdict": verdict, "text": _pick_text(rule, verdict, note)}

    if kind == "range":
        low, high = rule["low"], rule["high"]
        if low <= value <= high:
            return {"verdict": "pass", "text": rule.get("text_pass", note)}
        if value > high:
            return {"verdict": "warning", "text": rule.get("text_high", note)}
        return {"verdict": "warning", "text": rule.get("text_low", note)}

    if kind == "tiers":
        for tier in rule["tiers"]:
            matched = False
            if tier["op"] == "between":
                matched = tier["low"] <= value <= tier["high"]
            else:
                matched = {">": value > tier["value"], ">=": value >= tier["value"],
                           "<": value < tier["value"], "<=": value <= tier["value"]}[tier["op"]]
            if matched:
                return {"verdict": tier["verdict"], "text": tier.get("text", note)}
        return {"verdict": "fail", "text": note}

    if kind == "asset_light":
        benchmark = rule["benchmark_multiple"] * rule["benchmark_rate"] * 100  # 转 %
        if value >= benchmark:
            return {"verdict": "pass", "text": rule.get("text_pass", note)}
        return {"verdict": "fail", "text": rule.get("text_fail", note)}

    return {"verdict": "fail", "text": f"未注册的判定类型: {kind}"}


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def _summarize(items: list) -> dict:
    excluded = [it["id"] for it in items if it["verdict"] == "exclude"]
    failed = [it["id"] for it in items if it["verdict"] == "fail"]
    warned = [it["id"] for it in items if it["verdict"] == "warning"]
    pending = [it["id"] for it in items if it["verdict"] == "pending"]
    total = sum(it["earned"] for it in items)
    max_score = sum(it["score"] for it in items)

    if excluded:
        verdict = "exclude"
    elif failed:
        verdict = "fail"
    elif warned:
        verdict = "review"
    else:
        verdict = "pass"

    return {
        "verdict": verdict,
        "total_score": total,
        "max_score": max_score,
        "excluded": excluded,
        "failed": failed,
        "warnings": warned,
        "pending": pending,
    }


def compute(profile: dict, statements: dict) -> dict:
    """statements: {latest: st, y1: st, y2: st} → 逐项判定 + 汇总。"""
    items_out = []
    for item in profile["items"]:
        sid = item["id"]
        computed = _compute_value(sid, statements)
        judged = _apply_rule(item, computed["value"], computed["extra"])
        verdict = judged["verdict"]
        items_out.append({
            "id": sid,
            "name": item.get("name", sid),
            "unit": item.get("unit", ""),
            "score": item.get("score", 0),
            "value": _clean(computed["value"]),
            **({"extra": computed["extra"]} if computed["extra"] else {}),
            "verdict": verdict,
            "text": judged["text"],
            "earned": item.get("score", 0) if verdict == "pass" else 0,
            "note": item.get("note", ""),
        })
    return {
        "items": items_out,
        "summary": _summarize(items_out),
        "periods": PERIODS,
    }

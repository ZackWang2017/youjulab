"""指标公式注册表 + 打分/评级 —— 确定性计算，与 LLM 无关。

- 指标公式以 id 注册到 FORMULAS；analysis-profile.yaml 决定哪些指标参与、阈值与权重。
- 新增指标：在 FORMULAS 加一个函数（输入三期报表 dict，输出三期序列），并在配置里声明。
- 所有返回数值都会做 JSON 安全清理（NaN/Inf → None），可直接被 MCP 序列化。
"""

from __future__ import annotations

import math
from typing import Callable, Dict

from provider_repository import PERIODS


def _clean(v):
    """JSON 安全：NaN/Inf → None。"""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _clean_series(series: dict) -> dict:
    return {p: _clean(series.get(p)) for p in PERIODS}


def _pct_change(latest: float, prev: float):
    if prev in (None, 0):
        return None
    return (latest - prev) / prev * 100.0


# ---------------------------------------------------------------------------
# 指标公式：输入 {"latest": st, "y1": st, "y2": st}，输出三期序列 {"latest": v, "y1": v, "y2": v}
# 口径注释即公式文档；接真实 API 后如需更严谨口径（如平均净资产），改这里即可。
# ---------------------------------------------------------------------------

def _roe(st):
    # 净资产收益率：净利润 / 期末净资产 × 100（真实实现建议改用平均净资产）
    return {p: (s["net_profit"] / s["equity"] * 100.0 if s.get("equity") else None) for p, s in st.items()}


def _gross_margin(st):
    # 毛利率：(营收 - 营业成本) / 营收 × 100
    return {p: ((s["revenue"] - s["cogs"]) / s["revenue"] * 100.0 if s.get("revenue") else None) for p, s in st.items()}


def _net_margin(st):
    # 净利率：净利润 / 营收 × 100
    return {p: (s["net_profit"] / s["revenue"] * 100.0 if s.get("revenue") else None) for p, s in st.items()}


def _revenue_yoy(st):
    # 营收同比增速：latest vs y1、y1 vs y2
    return {
        "latest": _pct_change(st["latest"].get("revenue"), st["y1"].get("revenue")),
        "y1": _pct_change(st["y1"].get("revenue"), st["y2"].get("revenue")),
        "y2": None,
    }


def _profit_yoy(st):
    # 归母净利润同比增速
    return {
        "latest": _pct_change(st["latest"].get("net_profit"), st["y1"].get("net_profit")),
        "y1": _pct_change(st["y1"].get("net_profit"), st["y2"].get("net_profit")),
        "y2": None,
    }


def _debt_ratio(st):
    # 资产负债率：总负债 / 总资产 × 100
    return {p: (s["total_liab"] / s["total_assets"] * 100.0 if s.get("total_assets") else None) for p, s in st.items()}


def _interest_coverage(st):
    # 利息保障倍数：EBIT / 利息费用（EBIT ≈ 净利润 + 利息费用，税前口径简化）
    return {p: ((s["net_profit"] + s["interest_expense"]) / s["interest_expense"] if s.get("interest_expense") else None) for p, s in st.items()}


def _current_ratio(st):
    # 流动比率：流动资产 / 流动负债
    return {p: (s["current_assets"] / s["current_liab"] if s.get("current_liab") else None) for p, s in st.items()}


def _ocf_to_net_profit(st):
    # 经营现金流 / 净利润：盈利的现金含量
    return {p: (s["ocf"] / s["net_profit"] if s.get("net_profit") else None) for p, s in st.items()}


FORMULAS: Dict[str, Callable] = {
    "roe": _roe,
    "gross_margin": _gross_margin,
    "net_margin": _net_margin,
    "revenue_yoy": _revenue_yoy,
    "profit_yoy": _profit_yoy,
    "debt_ratio": _debt_ratio,
    "interest_coverage": _interest_coverage,
    "current_ratio": _current_ratio,
    "ocf_to_net_profit": _ocf_to_net_profit,
}


# ---------------------------------------------------------------------------
# 打分与评级
# ---------------------------------------------------------------------------

def _indicator_score(value, threshold, lower_better: bool = False):
    """value → [0,1]：达到 good 满分、低于 ok 零分、之间线性插值。"""
    if value is None:
        return None
    good, ok = threshold.get("good"), threshold.get("ok")
    if lower_better:  # 越低越好
        if value <= good:
            return 1.0
        if value >= ok:
            return 0.0
        return (ok - value) / (ok - good)
    if value >= good:
        return 1.0
    if value <= ok:
        return 0.0
    return (value - ok) / (good - ok)


def tier_of(value, threshold, lower_better: bool = False) -> str:
    if value is None:
        return "无数据"
    good, ok = threshold.get("good"), threshold.get("ok")
    if lower_better:
        return "优秀" if value <= good else ("及格" if value <= ok else "不及格")
    return "优秀" if value >= good else ("及格" if value >= ok else "不及格")


def compute(profile: dict, statements: dict) -> dict:
    """statements: {"latest": st, "y1": st, "y2": st} → 指标明细 + 维度得分 + 总分 + 评级。"""
    dim_weights = profile["scoring"]["dimension_weights"]
    cutoffs = profile["scoring"]["grade_cutoffs"]

    dim_results: dict = {}
    dim_scores: dict = {}
    for dim, dim_cfg in profile["indicators"].items():
        items = []
        for item in dim_cfg:
            fid = item["id"]
            if fid not in FORMULAS:
                items.append({**item, "series": None, "score": None, "tier": "未注册公式"})
                continue
            series = _clean_series(FORMULAS[fid](statements))
            value = series.get("latest")
            lower = bool(item["threshold"].get("lower_better", False))
            score = _indicator_score(value, item["threshold"], lower)
            items.append({
                **item,
                "series": series,
                "score": round(score, 4) if score is not None else None,
                "tier": tier_of(value, item["threshold"], lower),
            })
        scored = [it for it in items if it["score"] is not None]
        wsum = sum(it["in_dim_weight"] for it in scored)
        dim_score = (sum(it["score"] * it["in_dim_weight"] for it in scored) / wsum * 100.0) if scored else None
        dim_scores[dim] = round(dim_score, 2) if dim_score is not None else None
        dim_results[dim] = items

    scored_dims = [(d, s) for d, s in dim_scores.items() if s is not None]
    wsum = sum(dim_weights[d] for d, _ in scored_dims)
    total = (sum(dim_weights[d] * s for d, s in scored_dims) / wsum) if scored_dims else None

    grade = None
    if total is not None:
        for g, cutoff in sorted(cutoffs.items(), key=lambda kv: kv[1], reverse=True):
            if total >= cutoff:
                grade = g
                break

    return {
        "indicators": dim_results,
        "dimension_scores": dim_scores,
        "total_score": round(total, 2) if total is not None else None,
        "grade": grade,
    }

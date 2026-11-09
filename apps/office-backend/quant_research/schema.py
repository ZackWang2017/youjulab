from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import math
from datetime import date

FORMAT = "youju-strategy-v1"


def default_strategy(name: str = "") -> dict:
    return {"format": FORMAT, "objective": "", "code": "", "plan": None, "messages": [], "dataset": None}


def parse_prices(text: str, name: str = "prices.csv") -> dict:
    if len(text.encode("utf-8")) > 4_000_000:
        raise ValueError("数据文件不能超过 4 MB。")
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    header = next(reader, [])
    if not 2 <= len(header) <= 31 or header[0].strip().lower() != "date":
        raise ValueError("CSV 第一列必须为 date，其余为最多 30 个标的的正数价格或全收益净值。")
    symbols = [h.strip() for h in header[1:]]
    if any(not s or len(s) > 80 for s in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError("标的列名不能为空或重复。")
    rows = []
    previous = ""
    for number, raw in enumerate(reader, 2):
        if not raw or not any(v.strip() for v in raw):
            continue
        if len(raw) != len(header):
            raise ValueError(f"第 {number} 行列数不一致。")
        try:
            day = date.fromisoformat(raw[0].strip()).isoformat()
            values = [float(v) for v in raw[1:]]
        except ValueError as exc:
            raise ValueError(f"第 {number} 行日期或价格无效。") from exc
        if day <= previous or any(not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError(f"第 {number} 行日期必须递增且不重复，价格必须为正数，不能有缺失值。")
        previous = day
        rows.append([day, *values])
        if len(rows) > 12_000:
            raise ValueError("最多支持 12000 个交易日。")
    if len(rows) < 3:
        raise ValueError("至少需要 3 个交易日的数据。")
    payload = {"symbols": symbols, "rows": rows}
    return {"name": name[:100], **payload, "sha256": hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()}


def validate_plan(value: dict, symbols: list[str], *, check_code: bool = True) -> dict:
    if value.get("tool") not in {"bt_allocation", "python_research", "needs_input"}:
        raise ValueError("Agent 选择了未注册的工具。")
    if not isinstance(value.get("summary"), str) or len(value["summary"]) > 8000:
        raise ValueError("研究说明无效。")
    if value["tool"] == "needs_input":
        return {"tool": "needs_input", "summary": value["summary"], "code": "", "parameters": {}, "assumptions": []}
    code = value.get("code", "")
    if not isinstance(code, str) or (check_code and not code.strip()) or len(code) > 40_000:
        raise ValueError("研究程序为空或过长。")
    if check_code:
        try:
            ast.parse(code)
        except SyntaxError as exc:
            raise ValueError(f"Python 语法错误：{exc.msg}（第 {exc.lineno} 行）。") from exc
    params = value.get("parameters")
    if not isinstance(params, dict) or len(json.dumps(params, allow_nan=False)) > 20_000:
        raise ValueError("策略参数无效。")
    fee = params.get("fee_bps", 0)
    if type(fee) not in (int, float) or not math.isfinite(fee) or not 0 <= fee <= 100:
        raise ValueError("交易费率必须在 0～100 基点之间。")
    if value["tool"] == "bt_allocation":
        weights = params.get("weights")
        if not isinstance(weights, dict) or not weights or set(weights) - set(symbols):
            raise ValueError("组合权重必须引用已导入数据中的标的。")
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 or v > 1 for v in weights.values()) or abs(sum(weights.values()) - 1) > 1e-8:
            raise ValueError("权重必须在 0～1 之间且合计为 100%。")
        if params.get("rebalance") not in {"once", "monthly", "quarterly", "yearly"}:
            raise ValueError("再平衡频率无效。")
        fee = params.get("fee_bps", 0)
        if type(fee) not in (int, float) or not math.isfinite(fee) or not 0 <= fee <= 100:
            raise ValueError("交易费率必须在 0～100 基点之间。")
    assumptions = value.get("assumptions", [])
    if not isinstance(assumptions, list) or any(not isinstance(v, str) for v in assumptions):
        raise ValueError("研究假设格式无效。")
    return {"tool": value["tool"], "summary": value["summary"], "code": code, "parameters": params, "assumptions": assumptions[:20]}


def strategy_content(value: object) -> dict:
    if not isinstance(value, dict) or value.get("format") != FORMAT:
        raise ValueError("策略资产格式无效。")
    result = default_strategy()
    for key in ("objective", "code"):
        field = value.get(key, "")
        if not isinstance(field, str) or len(field) > (40_000 if key == "code" else 12_000):
            raise ValueError("研究说明或程序过长。")
        result[key] = field
    dataset = value.get("dataset")
    if dataset is not None:
        if not isinstance(dataset, dict):
            raise ValueError("研究数据格式无效。")
        raw = io.StringIO()
        writer = csv.writer(raw)
        writer.writerow(["date", *dataset.get("symbols", [])])
        writer.writerows(dataset.get("rows", []))
        result["dataset"] = parse_prices(raw.getvalue(), str(dataset.get("name", "prices.csv")))
        basis = dataset.get("basis", "")
        if basis not in {"price", "total_return"}:
            raise ValueError("请选择数据口径：价格或含分红全收益净值。")
        result["dataset"]["basis"] = basis
        result["dataset"]["source"] = str(dataset.get("source", "用户导入"))[:500]
        result["dataset"]["sha256"] = hashlib.sha256(json.dumps({"symbols": result["dataset"]["symbols"], "rows": result["dataset"]["rows"], "basis": basis}, separators=(",", ":")).encode()).hexdigest()
    if value.get("plan") is not None:
        result["plan"] = validate_plan(value["plan"], (result["dataset"] or {}).get("symbols", []), check_code=False)
    messages = value.get("messages", [])
    if not isinstance(messages, list):
        raise ValueError("研究会话格式无效。")
    result["messages"] = [{"role": m["role"], "content": str(m.get("content", ""))[:12000]} for m in messages[-30:] if isinstance(m, dict) and m.get("role") in {"user", "assistant"}]
    return result

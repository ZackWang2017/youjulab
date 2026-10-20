"""用共享表结构缓存让模型生成可直接渲染的图表计划。"""
from __future__ import annotations

import json
from typing import Any

from assistant import iter_chat_events, strip_thinking
from config import ROOT
from model_runtime import model_name
from sheet_cache import detail_surrounding, structure_fingerprint

CHART_TYPES = frozenset({"bar", "line", "pie", "scatter"})
MAX_SERIES = 8
MAX_POINTS = 500


async def generate_sheet_chart(cache: dict, description: str, selection: dict | None = None) -> dict:
    """生成图表配置；cache 与表格协作助手使用的是同一份共享缓存。"""
    messages = _chart_messages(cache, description, selection)
    raw = ""
    async for event in iter_chat_events(messages, json_object=True, stream=True, thinking=False):
        if event.get("type") == "delta" and event.get("part") != "reasoning":
            raw += str(event.get("text") or "")
    return _chart_response(cache, raw)


async def iter_sheet_chart_events(cache: dict, description: str, selection: dict | None = None):
    """流式输出真实推理过程，并在图表配置完整校验后发送 done。"""
    messages = _chart_messages(cache, description, selection)
    raw = ""
    async for event in iter_chat_events(messages, json_object=True, stream=True, thinking=True):
        if event.get("type") != "delta":
            continue
        text = str(event.get("text") or "")
        if event.get("part") == "reasoning":
            if text:
                yield {"type": "delta", "part": "reasoning", "text": text}
        else:
            raw += text
    yield {"type": "done", **_chart_response(cache, raw)}


def _chart_messages(cache: dict, description: str, selection: dict | None) -> list[dict]:
    intent = (description or "").strip() or "根据当前选区生成最合适的图表。"
    if len(intent) > 2000:
        raise ValueError("图表描述不能超过 2000 个字符。")
    if not cache:
        raise ValueError("尚未缓存表结构，请稍后重试。")
    payload = {
        "request": intent,
        "sheet": json.loads(detail_surrounding(cache, 90_000)),
        "profile": str(cache.get("profile") or "")[:4000],
        "selection": _selection_payload(selection),
    }
    skill = ROOT / "skills" / "spreadsheet-chart" / "SKILL.md"
    if not skill.is_file():
        raise ValueError("图表生成技能文件缺失。")
    return [
        {"role": "system", "content": skill.read_text(encoding="utf-8")},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _chart_response(cache: dict, raw: str) -> dict:
    plan = validate_chart_plan(_json_object(strip_thinking(raw)))
    return {
        "ok": True,
        "model_name": model_name() or "未配置",
        "context_source": "shared_sheet_cache",
        "profile_reused": bool(str(cache.get("profile") or "").strip()),
        "cache_revision": int(cache.get("revision") or 0),
        "cache_fingerprint": structure_fingerprint(cache),
        "chart": plan,
    }


def validate_chart_plan(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("模型没有返回有效的图表配置。")
    chart_type = str(raw.get("chart_type") or "").strip().lower()
    if chart_type not in CHART_TYPES:
        raise ValueError("模型返回了不支持的图表类型。")
    x_axis = raw.get("x_axis")
    if not isinstance(x_axis, dict) or not isinstance(x_axis.get("values"), list):
        raise ValueError("模型返回的 X 轴无效。")
    x_values = [_axis_value(item) for item in x_axis["values"][:MAX_POINTS]]
    if not x_values:
        raise ValueError("图表没有可用的 X 轴数据。")
    series_in = raw.get("series")
    if not isinstance(series_in, list) or not 1 <= len(series_in) <= MAX_SERIES:
        raise ValueError(f"图表系列数量应为 1 到 {MAX_SERIES}。")
    series: list[dict] = []
    for index, item in enumerate(series_in):
        if not isinstance(item, dict):
            raise ValueError("图表系列格式无效。")
        kind = str(item.get("type") or chart_type).strip().lower()
        if kind not in CHART_TYPES:
            raise ValueError("图表系列类型无效。")
        data = item.get("data")
        if not isinstance(data, list) or len(data) != len(x_values):
            raise ValueError("图表系列长度必须与 X 轴一致。")
        values = [_number_or_none(value) for value in data]
        if not any(value is not None for value in values):
            raise ValueError("图表系列没有可用数值。")
        series.append({
            "name": _text(item.get("name"), f"系列{index + 1}", 80),
            "type": kind,
            "data": values,
            "y_axis": 1 if item.get("y_axis") == 1 else 0,
        })
    if chart_type == "pie" and len(series) != 1:
        raise ValueError("饼图只能包含一个数据系列。")
    y_axes_in = raw.get("y_axes") if isinstance(raw.get("y_axes"), list) else []
    y_axes = []
    for index in range(2):
        source = y_axes_in[index] if index < len(y_axes_in) and isinstance(y_axes_in[index], dict) else {}
        if index == 1 and not any(item["y_axis"] == 1 for item in series):
            continue
        y_axes.append({
            "name": _text(source.get("name"), "数值" if index == 0 else "辅助轴", 80),
            "format": _format(source.get("format")),
        })
    return {
        "title": _text(raw.get("title"), "AI 生成图表", 120),
        "chart_type": chart_type,
        "source_range": _text(raw.get("source_range"), "", 120),
        "x_axis": {
            "name": _text(x_axis.get("name"), "", 80),
            "values": x_values,
        },
        "y_axes": y_axes or [{"name": "数值", "format": "number"}],
        "series": series,
        "summary": _text(raw.get("summary"), "", 300),
    }


def _selection_payload(selection: dict | None) -> dict | None:
    if not isinstance(selection, dict) or not str(selection.get("address") or "").strip():
        return None
    values = selection.get("values")
    if not isinstance(values, list):
        values = []
    return {
        "address": str(selection.get("address") or "")[:120],
        "row": selection.get("row"),
        "column": selection.get("column"),
        "rows": selection.get("rows"),
        "cols": selection.get("cols"),
        "values": values[:MAX_POINTS],
    }


def _json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        text = text[start:end + 1]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("模型没有返回可解析的图表配置。") from exc
    if not isinstance(value, dict):
        raise ValueError("模型没有返回有效的图表配置。")
    return value


def _axis_value(value: Any) -> str | float:
    if isinstance(value, bool) or value is None:
        return "" if value is None else str(value)
    if isinstance(value, (int, float)):
        return float(value)
    return str(value)[:200]


def _number_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    text = str(value).strip().replace(",", "")
    percent = text.endswith("%")
    if percent:
        text = text[:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return number / 100 if percent else number


def _format(value: Any) -> str:
    text = str(value or "number").strip().lower()
    return text if text in {"number", "percent", "currency", "integer"} else "number"


def _text(value: Any, fallback: str, limit: int) -> str:
    text = str(value or "").strip()
    return (text or fallback)[:limit]

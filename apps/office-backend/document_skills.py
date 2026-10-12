"""文档技能类型由后台维护。前端只按这里的字段渲染。"""
from __future__ import annotations

PRESET_SOURCES = (
    {"id": "hkex", "label": "港交所"},
    {"id": "sina", "label": "新浪财经"},
    {"id": "eastmoney", "label": "东方财富"},
    {"id": "xueqiu", "label": "雪球"},
    {"id": "jisilu", "label": "集思录"},
    {"id": "tencent", "label": "腾讯自选股"},
    {"id": "weread", "label": "微信读书"},
)
SKILL_TYPES: dict[str, dict] = {
    "news_brief": {
        "label": "资讯简报",
        "skill": "news-brief",
        "params": [
            {
                "key": "stocks",
                "label": "相关股票",
                "input": "lookup",
                "required": True,
                "placeholder": "输入名称或代码，从结果中选择，可多选",
            },
            {
                "key": "topic",
                "label": "主题",
                "input": "text",
                "required": True,
                "placeholder": "多个关键字用空格分隔，例如游戏 云业务",
            },
            {
                "key": "preset_sources",
                "label": "信息源",
                "input": "checks",
                "required": False,
                "options": list(PRESET_SOURCES),
            },
            {
                "key": "date_filter",
                "label": "时间范围",
                "input": "checks",
                "required": False,
                "options": [{"id": "today", "label": "仅今日"}],
            },
        ],
    },
}

MAX_NAME = 40


def list_skill_types() -> list[dict]:
    return [{"id": key, "label": spec["label"], "params": spec["params"]} for key, spec in SKILL_TYPES.items()]


def validate_skill(name: str, skill_type: str, params: dict | None) -> dict:
    title = (name or "").strip()
    if not title or len(title) > MAX_NAME:
        raise ValueError(f"名称需为 1–{MAX_NAME} 字。")
    spec = SKILL_TYPES.get((skill_type or "").strip())
    if not spec:
        raise ValueError("未知的技能类型。")
    incoming = params if isinstance(params, dict) else {}
    cleaned: dict[str, str] = {}
    for field in spec["params"]:
        raw = incoming.get(field["key"], "")
        if not isinstance(raw, str):
            raise ValueError(f"{field['label']}需为文本。")
        text = raw.strip()
        if field["key"] == "stocks":
            text = _normalize_stocks(text)
        elif field.get("input") == "checks":
            ids = [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]
            allowed = {option["id"] for option in field.get("options") or []}
            unknown = [part for part in ids if part not in allowed]
            if unknown:
                raise ValueError(f"未知的{field['label']}选项。")
            text = ",".join(dict.fromkeys(ids))
        elif field.get("required") and not text:
            raise ValueError(f"请填写{field['label']}。")
        cleaned[field["key"]] = text
    if spec is SKILL_TYPES["news_brief"] and not cleaned.get("preset_sources"):
        raise ValueError("请至少选择一项信息源。")
    return {"name": title, "type": skill_type.strip(), "params": cleaned}


def _normalize_stocks(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("请选择股票。")
    if "|" in raw or "\n" in raw:
        rows = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            code, _, name = line.partition("|")
            code = code.strip()
            name = name.strip()
            if not code or not name:
                raise ValueError("请从列表中选择股票。")
            rows.append(f"{code}|{name}")
        if not rows:
            raise ValueError("请选择股票。")
        if len(rows) > 10:
            raise ValueError("股票最多 10 只。")
        return "\n".join(rows)
    names = raw.split()
    if len(names) > 10:
        raise ValueError("股票最多 10 只。")
    return " ".join(names)


def public_skill(row: dict) -> dict:
    spec = SKILL_TYPES.get(row.get("type") or "", {})
    return {
        "id": row["id"],
        "project_id": row.get("project_id") or "",
        "document_id": row.get("document_id") or "",
        "name": row.get("name") or "",
        "type": row.get("type") or "",
        "type_label": spec.get("label") or row.get("type") or "",
        "params": row.get("params") or {},
        "updated_at": row.get("updated_at"),
    }

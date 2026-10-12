"""浏览器选区快照校验。"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class OfficeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=200)
    surface: Literal["sheet", "document", "pdf", "mindmap", "research", "diagram", "calendar"]
    revision: int = Field(ge=0)
    selection: dict
    surrounding: str = Field(default="", max_length=100000)

    @model_validator(mode="after")
    def check_selection(self):
        s = self.selection
        if len(json.dumps(s, ensure_ascii=False)) > 80000:
            raise ValueError("选区过大，请缩小范围。")
        if self.surface == "sheet":
            for key in ("row", "column", "rows", "cols"):
                if type(s.get(key)) is not int or s[key] < (1 if key in ("rows", "cols") else 0):
                    raise ValueError("表格选区无效。")
            if not isinstance(s.get("sheet_id"), str) or s["rows"] * s["cols"] > 2000:
                raise ValueError("选区不能超过 2000 个单元格。")
            values = s.get("values")
            if not isinstance(values, list) or len(values) != s["rows"]:
                raise ValueError("表格选区行数不匹配。")
            for row in values:
                if not isinstance(row, list) or len(row) != s["cols"]:
                    raise ValueError("表格选区列数不匹配。")
                if any(v is not None and type(v) not in (str, int, float, bool) for v in row):
                    raise ValueError("单元格值无效。")
        elif self.surface == "document":
            if not isinstance(s.get("text"), str):
                raise ValueError("请先选择文本。")
            if type(s.get("from")) is not int or type(s.get("to")) is not int or not 0 <= s["from"] <= s["to"]:
                raise ValueError("文档选区位置无效。")
            if s["from"] < s["to"] and not s["text"].strip():
                raise ValueError("请先选择文本。")
            if type(s.get("single_block")) is not bool:
                raise ValueError("缺少段落边界信息。")
        elif self.surface == "mindmap":
            if not isinstance(s.get("id"), str) or not s["id"].strip():
                raise ValueError("请先选中导图节点。")
            if not isinstance(s.get("text"), str):
                raise ValueError("导图节点文本无效。")
            if type(s.get("is_root")) is not bool:
                raise ValueError("缺少根节点标记。")
            path = s.get("path")
            if not isinstance(path, list) or any(not isinstance(p, str) for p in path):
                raise ValueError("导图节点路径无效。")
            parent_id = s.get("parent_id")
            if parent_id is not None and (not isinstance(parent_id, str) or not parent_id.strip()):
                raise ValueError("导图父节点无效。")
        elif self.surface in ("diagram", "research"):
            if not isinstance(s.get("text"), str):
                raise ValueError("绘图内容无效。")
            if len(s["text"]) > 40000:
                raise ValueError("绘图内容过长。")
        elif self.surface == "calendar":
            date = s.get("date")
            if not isinstance(date, str) or not _DATE.match(date):
                raise ValueError("请先选中日期。")
            if not isinstance(s.get("text"), str):
                raise ValueError("工作计划内容无效。")
            if len(s["text"]) > 40000:
                raise ValueError("当天内容过长。")
        else:
            if not isinstance(s.get("text"), str) or not s["text"].strip():
                raise ValueError("请先选择文本。")
            if type(s.get("page")) is not int or s["page"] < 1:
                raise ValueError("PDF 页码无效。")
        return self


def selection_has_content(context: OfficeContext) -> bool:
    s = context.selection
    if context.surface == "sheet":
        return any(v is not None and str(v).strip() != "" for row in s["values"] for v in row)
    if context.surface == "mindmap":
        return bool(str(s.get("text") or "").strip() or s.get("id"))
    if context.surface == "calendar":
        return bool(str(s.get("text") or "").strip())
    return bool(str(s.get("text") or "").strip())

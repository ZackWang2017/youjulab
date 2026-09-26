"""把表格选区二维数组发给用户指定的远程接口，并把返回解析成二维数组。"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

CELL_LIMIT = 10000
MAX_BODY = 2 * 1024 * 1024
TIMEOUT = 30.0
SCALAR = (str, int, float, bool, type(None))
ALLOWED_HEADER_PREFIXES = ("x-",)
ALLOWED_HEADERS = {"authorization", "content-type", "accept"}


def call_remote(
    url: str,
    values: Any,
    method: str = "POST",
    function: str = "",
    headers: Any = None,
    client: Any = None,
) -> list[list[Any]]:
    target = parse_url(url)
    verb = parse_method(method)
    matrix = normalize_values(values)
    extra = parse_headers(headers)
    fn = (function or "").strip()
    payload = {"values": matrix}
    if fn:
        payload["function"] = fn
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_BODY:
        raise ValueError("选区太大，请缩小后再调用。")
    request_headers = {"Accept": "application/json"}
    request_headers.update(extra)
    owns_client = client is None
    http = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    try:
        if verb == "GET":
            params: dict[str, str] = {}
            if fn:
                params["function"] = fn
            encoded = json.dumps(matrix, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > 8000:
                raise ValueError("选区较大时请使用 POST。")
            params["values"] = encoded
            response = http.request("GET", target, params=params, headers=request_headers)
        else:
            request_headers.setdefault("Content-Type", "application/json; charset=utf-8")
            response = http.request(verb, target, content=body, headers=request_headers)
    except httpx.TimeoutException as exc:
        raise ValueError("远程接口超时，请稍后重试。") from exc
    except httpx.RequestError as exc:
        raise ValueError("无法连接远程接口，请检查地址。") from exc
    finally:
        if owns_client:
            http.close()
    if len(response.content or b"") > MAX_BODY:
        raise ValueError("远程接口返回内容过大。")
    if response.status_code >= 400:
        raise ValueError(_remote_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError("远程接口没有返回 JSON。") from exc
    return matrix_from_payload(data)


def parse_url(url: str) -> str:
    text = (url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("地址必须是完整的 http 或 https 链接。")
    return text


def parse_method(method: str) -> str:
    verb = (method or "POST").strip().upper()
    if verb not in ("GET", "POST", "PUT"):
        raise ValueError("请求方法只支持 GET、POST 或 PUT。")
    return verb


def parse_headers(raw: Any) -> dict[str, str]:
    if raw in (None, "", {}):
        return {}
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("请求头必须是 JSON 对象。") from exc
    if not isinstance(raw, dict):
        raise ValueError("请求头必须是 JSON 对象。")
    headers: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        lowered = name.lower()
        if lowered not in ALLOWED_HEADERS and not lowered.startswith(ALLOWED_HEADER_PREFIXES):
            raise ValueError("请求头只支持 Authorization、Content-Type、Accept 或以 X- 开头的字段。")
        if value is None:
            continue
        text = str(value).strip()
        if text:
            headers[name] = text
    return headers


def normalize_values(values: Any) -> list[list[Any]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("选区必须是二维数组。")
    rows: list[list[Any]] = []
    width = 0
    for row in values:
        if not isinstance(row, list):
            raise ValueError("选区每一行都必须是数组。")
        cells = [_as_cell(cell) for cell in row]
        width = max(width, len(cells))
        rows.append(cells)
    if width < 1 and rows:
        rows = [[None] for _ in rows]
        width = 1
    for row in rows:
        while len(row) < width:
            row.append(None)
    count = max(len(rows) * width, 0)
    if count > CELL_LIMIT:
        raise ValueError(f"一次最多处理 {CELL_LIMIT} 个单元格。")
    return rows


def matrix_from_payload(data: Any) -> list[list[Any]]:
    if isinstance(data, dict):
        if isinstance(data.get("error"), str) and data["error"].strip():
            raise ValueError(data["error"].strip())
        for key in ("values", "data", "result", "rows"):
            if key in data:
                return matrix_from_payload(data[key])
        if not data:
            return []
        raise ValueError("远程接口必须返回二维数组，或带 values 字段的对象。")
    if not isinstance(data, list):
        raise ValueError("远程接口必须返回二维数组。")
    if not data:
        return []
    if all(isinstance(row, list) for row in data):
        return normalize_values(data)
    if all(isinstance(row, dict) for row in data):
        keys: list[str] = []
        for row in data:
            for key in row:
                if key not in keys:
                    keys.append(str(key))
        return normalize_values([keys] + [[row.get(key) for key in keys] for row in data])
    return normalize_values([[item] for item in data])


def _as_cell(value: Any) -> Any:
    if type(value) in SCALAR:
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _remote_error(response: httpx.Response) -> str:
    snippet = (response.text or "").strip().replace("\n", " ")
    if len(snippet) > 180:
        snippet = snippet[:180] + "…"
    if snippet:
        return f"远程接口返回 {response.status_code}：{snippet}"
    return f"远程接口返回 {response.status_code}。"

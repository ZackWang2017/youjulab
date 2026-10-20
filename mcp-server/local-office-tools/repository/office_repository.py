"""访问 office-backend。服务间用 MCP 静态令牌鉴权，用户身份放在 X-User-Access-Token。"""
from __future__ import annotations

import os
import json
from typing import Any, Callable

import httpx

from access_token import incoming_access_token, mcp_access_token

ClientFactory = Callable[[], Any]
_factory: ClientFactory | None = None


class OfficeError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def office_api_url() -> str:
    raw = (os.environ.get("OFFICE_API_URL") or "").strip()
    if raw:
        return raw.rstrip("/")
    host = os.environ.get("OFFICE_API_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = os.environ.get("OFFICE_API_PORT", "8100").strip() or "8100"
    return f"http://{host}:{port}"


def set_client_factory(factory: ClientFactory | None) -> None:
    global _factory
    _factory = factory


def client_for() -> Any:
    if _factory:
        return _factory()
    token = incoming_access_token()
    if not token:
        raise OfficeError(401, "请提供用户访问令牌。")
    return OfficeClient(office_api_url(), token)


class OfficeClient:
    def __init__(self, base_url: str, token: str, http: Any | None = None):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.http = http or httpx.Client(timeout=30.0)

    def call(self, method: str, path: str, body: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        kwargs: dict[str, Any] = {
            "headers": {
                "Authorization": f"Bearer {mcp_access_token()}",
                "X-User-Access-Token": self.token,
                "Content-Type": "application/json",
            },
        }
        if body is not None:
            kwargs["json"] = body
        response = self.http.request(method, url, **kwargs)
        if response.status_code == 401:
            raise OfficeError(401, "令牌无效或已过期。")
        if response.status_code >= 400:
            message = _error_message(response)
            raise OfficeError(response.status_code, message)
        if response.status_code == 204 or not getattr(response, "content", None):
            return None
        return response.json()

    def me(self) -> dict:
        return self.call("GET", "/auth/me")

    def projects(self) -> list[dict]:
        return list(self.call("GET", "/projects").get("projects") or [])

    def create_project(self, name: str) -> dict:
        return self.call("POST", "/projects", {"name": name})

    def assets(self, project_id: str, kind: str = "sheet") -> list[dict]:
        return list(self.call("GET", f"/projects/{project_id}/assets?kind={kind}").get("assets") or [])

    def create_asset(self, project_id: str, name: str, kind: str = "sheet") -> dict:
        return self.call("POST", f"/projects/{project_id}/assets", {"kind": kind, "name": name})

    def get_asset(self, asset_id: str) -> dict:
        return self.call("GET", f"/assets/{asset_id}")

    def save_asset(self, asset_id: str, content: dict, revision: int, name: str | None = None) -> dict:
        body: dict[str, Any] = {"content": content, "revision": revision}
        if name is not None:
            body["name"] = name
        return self.call("PUT", f"/assets/{asset_id}", body)

    def ensure_pdf_index(self, document_id: str) -> dict:
        return self.call("POST", f"/internal/pdf/{document_id}/index", {})

    def pdf_index_status(self, document_id: str) -> dict:
        return self.call("GET", f"/internal/pdf/{document_id}/index")

    def search_pdf(self, document_id: str, query: str, top_k: int = 8) -> dict:
        return self.call("POST", f"/internal/pdf/{document_id}/search", {"query": query, "top_k": top_k})

    def generate_pdf_outline(self, document_id: str) -> dict:
        """Consume the office-backend SSE pipeline and return its final event to an MCP caller."""
        url = f"{self.base_url}/assets/{document_id}/outline/generate"
        headers = {
            "Authorization": f"Bearer {mcp_access_token()}",
            "X-User-Access-Token": self.token,
            "Content-Type": "application/json",
        }
        if not hasattr(self.http, "stream"):
            raise OfficeError(500, "当前 HTTP 客户端不支持 PDF 大纲流式接口。")
        with self.http.stream("POST", url, headers=headers) as response:
            if response.status_code >= 400:
                raise OfficeError(response.status_code, _error_message(response))
            for line in response.iter_lines():
                text = line.decode() if isinstance(line, bytes) else str(line)
                if not text.startswith("data:"):
                    continue
                try:
                    event = json.loads(text[5:].strip())
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "error":
                    raise OfficeError(400, str(event.get("error") or "生成 PDF 大纲失败。"))
                if event.get("type") == "done":
                    return event
        raise OfficeError(502, "PDF 大纲服务未返回结果。")


def _error_message(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        return response.text or f"office-backend HTTP {response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error") or payload.get("detail")
        if isinstance(error, str) and error:
            return error
    return response.text or f"office-backend HTTP {response.status_code}"

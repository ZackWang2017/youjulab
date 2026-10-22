"""POST /api/embeddings。与账号接口同一进程，默认 http://127.0.0.1:8000。"""
from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse

import embedding_service as service
from embedding_service import EmbeddingError


def register_routes(app, cors: dict) -> None:
    async def create(request: Request):
        if request.method == "OPTIONS":
            return JSONResponse({}, headers=cors)
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            return JSONResponse({"error": "请求体不是合法 JSON。"}, status_code=400, headers=cors)
        raw = body.get("input") if isinstance(body, dict) else None
        texts = [raw] if isinstance(raw, str) else [str(item) for item in raw] if isinstance(raw, list) else []
        if len(texts) > 256:
            return JSONResponse({"error": "单次最多 256 条文本。"}, status_code=400, headers=cors)
        try:
            vectors = service.embed_texts(texts)
        except EmbeddingError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=cors)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"embedding 失败：{exc}"}, status_code=502, headers=cors)
        return JSONResponse({
            "object": "list",
            "model": service.model_name(),
            "data": [
                {"object": "embedding", "index": index, "embedding": vector}
                for index, vector in enumerate(vectors)
            ],
        }, headers=cors)

    app.add_route("/api/embeddings", create, methods=["POST", "OPTIONS"])

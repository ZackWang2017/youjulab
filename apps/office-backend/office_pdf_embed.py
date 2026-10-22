"""PDF 块向量：文本发到远程 mcp /api/embeddings，向量留在本机 sqlite-vec。"""
from __future__ import annotations

import httpx

from config import embedding_dim, mcp_remote_base
from remote_auth import remote_token


class EmbedError(ValueError):
    pass


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    token = remote_token()
    if not token:
        raise EmbedError("embedding 需要登录后才能调用远程服务。")
    dim = embedding_dim()
    url = f"{mcp_remote_base()}/api/embeddings"
    timeout = httpx.Timeout(30.0, read=180.0)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=timeout) as client:
        res = client.post(url, headers=headers, json={"input": texts})
    if res.status_code == 401:
        raise EmbedError("embedding 需要登录后才能调用远程服务。")
    if res.is_error:
        raise EmbedError(f"Embedding HTTP {res.status_code}：{res.text[:300]}")
    items = res.json().get("data")
    if not isinstance(items, list) or len(items) != len(texts):
        raise EmbedError("Embedding 返回条数不匹配。")
    items = sorted(items, key=lambda item: item.get("index", 0))
    out: list[list[float]] = []
    for item in items:
        vec = item.get("embedding")
        if not isinstance(vec, list) or len(vec) != dim:
            raise EmbedError(f"embedding 维度异常，期望 {dim}。")
        out.append([float(x) for x in vec])
    return out

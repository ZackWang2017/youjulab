"""Desktop-only, fixed-target MCP bridge; preserves streaming responses."""
from urllib.parse import urlparse

import httpcore
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse


def _default_pool() -> httpcore.AsyncConnectionPool:
    # 不用 httpx.AsyncClient：它在构造时就创建 SSLContext，打包态 sidecar 会因此报 Errno 2。
    # httpcore 只在连接 https 上游时才创建 SSL 上下文。
    return httpcore.AsyncConnectionPool(
        max_connections=20,
        keepalive_expiry=30,
        retries=1,
    )


def register_routes(app, base_provider, pool_factory=_default_pool):
    pool = pool_factory()

    @app.api_route('/desktop-mcp/api/{remote_path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    async def forward(request: Request, remote_path: str):
        origin = request.headers.get('origin')
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port not in (request.url.port, 5173):
                raise HTTPException(403, '仅允许本机桌面页面访问。')
        if any(part in ('.', '..') for part in remote_path.split('/')) or '%' in remote_path:
            raise HTTPException(400, '无效的服务路径。')
        base = base_provider().rstrip('/')
        parsed = urlparse(base)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise HTTPException(503, '远程服务地址配置无效，请检查连接设置。')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 32 * 1024**2:
                raise HTTPException(413, '请求内容过大。')
        # httpcore 不像 httpx 那样自动补 Host / Content-Length。
        headers = [(b'host', parsed.netloc.encode('latin-1'))]
        headers += [
            (key.encode('latin-1'), request.headers[key].encode('latin-1'))
            for key in ('authorization', 'content-type', 'accept', 'range')
            if key in request.headers
        ]
        if body or request.method in ('POST', 'PUT', 'PATCH'):
            headers.append((b'content-length', str(len(body)).encode('ascii')))
        url = base + '/api/' + remote_path
        if request.url.query:
            url += '?' + request.url.query
        upstream_request = httpcore.Request(
            request.method,
            url,
            headers=headers,
            content=bytes(body),
            extensions={'timeout': {'connect': 15, 'read': 180, 'write': 15, 'pool': 15}},
        )
        try:
            upstream = await pool.handle_async_request(upstream_request)
        except (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError, OSError):
            return JSONResponse({'detail': '无法连接认证或数据服务，请检查 MCP 地址及网络。'}, status_code=502)

        async def stream():
            try:
                async for chunk in upstream.aiter_stream():
                    yield chunk
            finally:
                await upstream.aclose()

        wanted = {b'content-type', b'content-disposition', b'content-range', b'accept-ranges'}
        outgoing = {
            key.decode('latin-1'): value.decode('latin-1')
            for key, value in upstream.headers
            if key.lower() in wanted
        }
        outgoing['Cache-Control'] = 'no-store'
        return StreamingResponse(stream(), status_code=upstream.status, headers=outgoing)

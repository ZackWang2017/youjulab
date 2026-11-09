import unittest

import httpcore
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktop_proxy import register_routes


class DesktopProxyTests(unittest.TestCase):
    def test_fixed_target_auth_query_and_streaming(self):
        seen = []

        class Pool:
            async def handle_async_request(self, request):
                seen.append(request)
                return httpcore.Response(200, content=b'data: hello\n\n', headers=[(b'content-type', b'text/event-stream')])

        app = FastAPI()
        register_routes(app, lambda: 'http://192.168.0.5:8000',
                        Pool)
        with TestClient(app, base_url='http://127.0.0.1:8100') as client:
            response = client.post('/desktop-mcp/api/account/login?x=1', content=b'{}',
                                   headers={'Authorization': 'Bearer test', 'Origin': 'http://127.0.0.1:8100'})
            self.assertEqual(response.content, b'data: hello\n\n')
            self.assertEqual(seen[0].url.target, b'/api/account/login?x=1')
            self.assertEqual(dict(seen[0].headers)[b'authorization'], b'Bearer test')
            self.assertNotIn(b'origin', dict(seen[0].headers))
            self.assertEqual(client.get('/desktop-mcp/api/account/health', headers={'Origin': 'https://evil.example'}).status_code, 403)

    def test_network_error_is_readable(self):
        class Pool:
            async def handle_async_request(self, request):
                raise httpcore.ConnectError('unavailable')

        app = FastAPI()
        register_routes(app, lambda: 'http://192.168.0.5:8000',
                        Pool)
        with TestClient(app) as client:
            self.assertEqual(client.get('/desktop-mcp/api/account/health').status_code, 502)

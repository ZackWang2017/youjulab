import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from server import create_app
from store import MemoryStore
from web_document import MAX_BYTES, fetch_html, parse_article, public_target, safe_link


ARTICLE = '''<html><head><title>示例文章</title></head><body><nav>导航广告</nav>
<h1 id="activity-name">示例文章</h1><span id="js_name">研究公众号</span><em id="publish_time">2026-11-02</em>
<div id="js_content"><p>这是一段用于测试的公开文章正文，保持原始事实，不使用模型改写内容。</p>
<section><h2>业务结构</h2><p><strong>利润</strong>与<em>现金流</em>，都需要审慎分析。<br>这是新的一行。</p></section>
<ul><li>第一项<ul><li>子项</li></ul></li><li>第二项</li></ul>
<ol><li>编号内容</li></ol><blockquote><p>引用原文</p></blockquote>
<table><tr><th>项目</th><th>金额</th></tr><tr><td>收入</td><td>100</td></tr></table>
<p><a href="javascript:alert(1)">危险链接</a><a href="/reference">安全链接</a>
<img data-src="https://images.example.com/figure.png" alt="业务图"></p>
<script>alert('unsafe')</script><div hidden>隐藏内容</div></div><footer>页脚广告</footer></body></html>'''


class WebDocumentTests(unittest.TestCase):
    def test_wechat_structure_source_and_safe_content(self):
        doc = parse_article(ARTICLE, 'https://mp.weixin.qq.com/s/example')
        text = json.dumps(doc, ensure_ascii=False)
        self.assertEqual(doc['content'][0]['content'][0]['text'], '示例文章')
        for expected in ['bulletList', 'orderedList', 'listItem', 'tableHeader', 'tableCell',
                         'blockquote', 'hardBreak', 'bold', 'italic', '研究公众号', '2026-11-02',
                         'https://mp.weixin.qq.com/reference', '[图片：业务图]']:
            self.assertIn(expected, text)
        for forbidden in ['javascript:', 'alert(', '隐藏内容', '导航广告', '页脚广告']:
            self.assertNotIn(forbidden, text)

    def test_generic_article_omits_navigation_and_keeps_unicode(self):
        html = '<nav>导航</nav><article><h1>公司分析</h1><p>' + '收入增长需要进一步验证。' * 8 + '</p></article><footer>广告</footer>'
        doc = parse_article(html, 'https://example.com/article')
        self.assertEqual(doc['content'][0]['content'][0]['text'], '公司分析')
        self.assertNotIn('导航', json.dumps(doc, ensure_ascii=False))

    def test_wechat_initially_hidden_content_is_preserved(self):
        html = ARTICLE.replace('id="js_content"', 'id="js_content" style="visibility: hidden; opacity: 0"')
        doc = parse_article(html, 'https://mp.weixin.qq.com/s/example')
        self.assertIn('公开文章正文', json.dumps(doc, ensure_ascii=False))
        self.assertNotIn('隐藏内容', json.dumps(doc, ensure_ascii=False))

    def test_wechat_hidden_ancestor_does_not_remove_article(self):
        html = ARTICLE.replace('<body>', '<body style="visibility:hidden">')
        doc = parse_article(html, 'https://mp.weixin.qq.com/s/example')
        self.assertIn('公开文章正文', json.dumps(doc, ensure_ascii=False))

    def test_picks_full_article_instead_of_first_teaser(self):
        html = '<article>短摘要</article><article><p>' + '真正的文章正文。' * 20 + '</p></article>'
        doc = parse_article(html, 'https://example.com')
        self.assertIn('真正的文章正文', json.dumps(doc, ensure_ascii=False))

    def test_short_and_oversized_pages_have_distinct_errors(self):
        with self.assertRaisesRegex(ValueError, '仅提取到 3 个正文字符'):
            parse_article('<title>短页面</title><article>短正文</article>', 'https://example.com')
        with self.assertRaisesRegex(ValueError, '超过 250,000'):
            parse_article('<article>' + '字' * 250001 + '</article>', 'https://example.com')

    def test_does_not_truncate_long_articles(self):
        text = '需要保留的长文。' * 1500
        doc = parse_article('<article><p>' + text + '</p></article>', 'https://example.com')
        self.assertEqual(doc['content'][-1]['content'][0]['text'], text)

    def test_missing_body_and_challenge_are_rejected(self):
        for html, url in [('<title>环境异常</title><p>请验证</p>', 'https://mp.weixin.qq.com/s/x'),
                          ('<title>Just a moment</title><article>' + '验证内容' * 30 + '</article>', 'https://example.com'),
                          ('<div id="app"></div>', 'https://example.com')]:
            with self.assertRaises(ValueError):
                parse_article(html, url)

    def test_only_public_addresses_and_safe_ports(self):
        for ip in ['127.0.0.1', '10.0.0.1', '192.168.0.5', '169.254.169.254', '::1', 'fc00::1', '::ffff:127.0.0.1']:
            with patch('web_document.socket.getaddrinfo', return_value=[(socket.AF_INET, 1, 6, '', (ip, 443))]):
                with self.assertRaises(ValueError):
                    public_target('https://example.com')
        for url in ['file:///etc/passwd', 'https://user:password@example.com', 'http://example.com:8100', 'https://example.com/\r\nx']:
            with self.assertRaises(ValueError):
                public_target(url)
        with patch('web_document.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]):
            self.assertEqual(public_target('https://example.com')[3], '93.184.216.34')

    def test_mixed_public_and_private_dns_is_rejected(self):
        with patch('web_document.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443)), (2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                public_target('https://example.com')

    def test_redirect_cannot_access_internal_address(self):
        response = MagicMock(status=302)
        response.getheader.side_effect = lambda key, default=None: 'http://127.0.0.1/private' if key == 'Location' else default
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch('web_document.public_target', side_effect=[('https', 'example.com', 443, '93.184.216.34'), ValueError('内网地址')]), \
             patch('web_document.http.client.HTTPSConnection', return_value=connection):
            with self.assertRaisesRegex(ValueError, '内网'):
                fetch_html('https://example.com')
        connection.close.assert_called_once()

    def test_transport_pins_validated_address_and_limits_response(self):
        response = MagicMock(status=200)
        headers = {'Content-Type': 'text/html; charset=utf-8', 'Content-Length': str(MAX_BYTES + 1)}
        response.getheader.side_effect = lambda key, default=None: headers.get(key, default)
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch('web_document.public_target', return_value=('https', 'example.com', 443, '93.184.216.34')), \
             patch('web_document.http.client.HTTPSConnection', return_value=connection), \
             patch('web_document.socket.create_connection') as connect:
            with self.assertRaisesRegex(ValueError, '4 MB'):
                fetch_html('https://example.com')
            connection._create_connection(('example.com', 443), 5)
            connect.assert_called_once_with(('93.184.216.34', 443), 5, None)
        connection.close.assert_called_once()

    def test_no_unsafe_or_empty_links(self):
        for link in ['', 'javascript:alert(1)', 'data:text/html,test', 'file:///etc/passwd']:
            self.assertIsNone(safe_link(link, 'https://example.com'))

    def test_fake_ip_has_actionable_error(self):
        with patch('web_document.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('198.18.0.67', 443))]):
            with self.assertRaisesRegex(ValueError, 'Fake-IP'):
                public_target('https://example.com')

    def test_successful_fetch_decodes_html_and_sends_no_credentials(self):
        response = MagicMock(status=200)
        response.getheader.side_effect = lambda key, default=None: 'text/html; charset=utf-8' if key == 'Content-Type' else default
        response.read1.side_effect = [ARTICLE.encode(), b'']
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch('web_document.public_target', return_value=('https', 'example.com', 443, '93.184.216.34')), \
             patch('web_document.http.client.HTTPSConnection', return_value=connection):
            html, url = fetch_html('https://example.com/中文?query=中文')
        self.assertEqual(html, ARTICLE)
        self.assertIn('example.com', url)
        args, kwargs = connection.request.call_args
        self.assertNotIn('中文', args[1])
        self.assertNotIn('Cookie', kwargs['headers'])
        self.assertNotIn('Authorization', kwargs['headers'])


class WebDocumentApiTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.client = TestClient(create_app(self.store))
        auth = self.client.post('/auth/register', json={'email': 'collector@example.com', 'password': 'secret1'}).json()
        self.headers = {'Authorization': 'Bearer ' + auth['token']}
        self.project = auth['user']['last_project_id']

    def test_import_create_visibility_and_persistence(self):
        with patch('web_document.fetch_html', return_value=(ARTICLE, 'https://mp.weixin.qq.com/s/example')):
            response = self.client.post(f'/projects/{self.project}/assets', headers=self.headers, json={
                'kind': 'document', 'name': '公众号收藏', 'visibility': 'user', 'source_url': 'https://mp.weixin.qq.com/s/example',
            })
        self.assertEqual(response.status_code, 200, response.text)
        asset = response.json()
        self.assertEqual(asset['placement'], 'user')
        loaded = self.client.get('/assets/' + asset['id'], headers=self.headers).json()
        self.assertEqual(loaded['content'], asset['content'])
        self.assertIn('研究公众号', json.dumps(loaded, ensure_ascii=False))

    def test_failure_does_not_create_asset_and_unauthorized_does_not_fetch(self):
        with patch('server.import_web_document', side_effect=ValueError('验证码')) as fetch:
            url = f'/projects/{self.project}/assets'
            body = {'kind': 'document', 'name': '收藏', 'source_url': 'https://example.com'}
            self.assertEqual(self.client.post(url, json=body).status_code, 401)
            fetch.assert_not_called()
            response = self.client.post(url, headers=self.headers, json=body)
            self.assertEqual(response.status_code, 400)
            self.assertIn('验证码', response.text)
        documents = self.client.get(f'/projects/{self.project}/assets?kind=document', headers=self.headers).json()['assets']
        self.assertFalse(any(asset['name'] == '收藏' for asset in documents))

    def test_wrong_project_and_wrong_asset_kind_do_not_fetch(self):
        with patch('server.import_web_document') as fetch:
            response = self.client.post('/projects/missing/assets', headers=self.headers, json={
                'kind': 'document', 'name': '收藏', 'source_url': 'https://example.com',
            })
            self.assertEqual(response.status_code, 404)
            response = self.client.post(f'/projects/{self.project}/assets', headers=self.headers, json={
                'kind': 'sheet', 'name': '收藏', 'source_url': 'https://example.com',
            })
            self.assertEqual(response.status_code, 400)
            fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()

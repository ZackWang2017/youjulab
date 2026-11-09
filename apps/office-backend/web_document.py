"""Bounded public-web import; deterministic HTML to the document editor's JSON."""
from __future__ import annotations

import http.client
import ipaddress
import logging
import re
import socket
import ssl
import time
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString, Tag

MAX_BYTES = 4 * 1024 * 1024
MAX_TEXT = 250_000
TIMEOUT = 25
SKIP = 'script,style,noscript,iframe,object,embed,svg,canvas,nav,footer,header,form,button,input,select,textarea'
logger = logging.getLogger(__name__)


def public_target(url: str) -> tuple[str, str, int, str]:
    try:
        parts = urlsplit(url.strip())
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            raise ValueError()
        host = parts.hostname.encode('idna').decode('ascii')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        if port not in {80, 443} or '%' in host or any(ord(c) < 33 for c in url):
            raise ValueError()
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = [entry[4][0] for entry in addresses]
        if any(ipaddress.ip_address(ip) in ipaddress.ip_network('198.18.0.0/15') for ip in ips if ':' not in ip):
            raise ValueError('DNS 返回了代理 Fake-IP 地址。请在代理软件中对目标网站关闭 Fake-IP 后重试。')
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError()
    except (ValueError, UnicodeError, OSError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith('DNS 返回'):
            raise
        raise ValueError('仅支持可公开访问的 HTTP/HTTPS 网页，不允许本机、内网地址或特殊端口。') from exc
    return parts.scheme, host, port, ips[0]


def fetch_html(url: str) -> tuple[str, str]:
    deadline = time.monotonic() + TIMEOUT
    try:
        for _ in range(6):
            scheme, host, port, ip = public_target(url)
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError()
            connection = (http.client.HTTPSConnection(host, port, timeout=min(left, 10), context=ssl.create_default_context())
                          if scheme == 'https' else http.client.HTTPConnection(host, port, timeout=min(left, 10)))
            # Pin the validated address; TLS certificate/SNI still use the original hostname.
            connection._create_connection = lambda address, timeout, source_address=None: socket.create_connection((ip, port), timeout, source_address)
            try:
                parts = urlsplit(url)
                path = quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~")
                query = quote(parts.query, safe="/?%:@!$&'()*+,;=-._~")
                connection.request('GET', urlunsplit(('', '', path, query, '')), headers={
                    'User-Agent': 'Mozilla/5.0 (compatible; YoujulabArticleCollector/1.0)',
                    'Accept': 'text/html,application/xhtml+xml', 'Accept-Encoding': 'identity',
                })
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader('Location')
                    if not location:
                        raise ValueError('网页重定向缺少目标地址。')
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise ValueError(f'网页返回 HTTP {response.status}，未创建文档。')
                media_type = response.getheader('Content-Type', '').split(';')[0].strip().lower()
                if media_type not in {'text/html', 'application/xhtml+xml'}:
                    raise ValueError('该链接不是 HTML 文章网页；PDF 请使用 PDF 上传。')
                if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                    raise ValueError('网页返回了不支持的压缩内容。')
                size = response.getheader('Content-Length', '')
                if size.isdigit() and int(size) > MAX_BYTES:
                    raise ValueError('网页超过 4 MB，请改用手动摘录。')
                chunks, total = [], 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    if connection.sock:
                        connection.sock.settimeout(min(remaining, 10))
                    chunk = response.read1(min(65536, MAX_BYTES + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError('网页超过 4 MB，请改用手动摘录。')
                    chunks.append(chunk)
                content = b''.join(chunks)
                charset = re.search(r'charset\s*=\s*["\']?([\w-]+)', response.getheader('Content-Type', ''), re.I)
                if not charset:
                    charset = re.search(rb'charset\s*=\s*["\']?([\w-]+)', content[:8192], re.I)
                encoding = charset.group(1) if charset else 'utf-8'
                if isinstance(encoding, bytes):
                    encoding = encoding.decode('ascii')
                try:
                    return content.decode(encoding, errors='replace'), url
                except LookupError:
                    return content.decode('utf-8', errors='replace'), url
            finally:
                connection.close()
        raise ValueError('网页重定向次数过多。')
    except ValueError:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise ValueError('网页抓取失败或超时，请检查链接；需要登录、验证码或浏览器运行的网页暂不支持。') from exc


def safe_link(value: str, base: str) -> str | None:
    if not value.strip():
        return None
    try:
        url = urljoin(base, value.strip())
        p = urlsplit(url)
        if p.scheme not in {'http', 'https'} or not p.hostname or p.username or p.password:
            return None
        return url
    except ValueError:
        return None


def _text(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip()


def _inline(node, base: str, marks: list | None = None, depth: int = 0) -> list:
    if depth > 40:
        raise ValueError('网页结构过深，暂不支持导入。')
    marks = marks or []
    if isinstance(node, NavigableString):
        text = re.sub(r'\s+', ' ', str(node))
        return ([{'type': 'text', 'text': text, **({'marks': marks} if marks else {})}] if text else [])
    if not isinstance(node, Tag):
        return []
    if node.name == 'br':
        return [{'type': 'hardBreak'}]
    mark = {'strong': 'bold', 'b': 'bold', 'em': 'italic', 'i': 'italic', 's': 'strike', 'code': 'code'}.get(node.name)
    if not mark and re.search(r'font-weight\s*:\s*(?:bold|[6-9]00)', node.get('style', ''), re.I):
        mark = 'bold'
    if mark and not any(m['type'] == mark for m in marks):
        marks = [*marks, {'type': mark}]
    if node.name == 'a' and (href := safe_link(node.get('href', ''), base)):
        marks = [m for m in marks if m['type'] != 'link'] + [{'type': 'link', 'attrs': {'href': href, 'target': '_blank', 'rel': 'noopener noreferrer'}}]
    if node.name == 'img':
        href = safe_link(node.get('data-src') or node.get('src') or '', base)
        if not href:
            return []
        return [{'type': 'text', 'text': f"[图片：{node.get('alt') or '查看原图'}]", 'marks': [
            {'type': 'link', 'attrs': {'href': href, 'target': '_blank', 'rel': 'noopener noreferrer'}},
        ]}]
    return [part for child in node.children for part in _inline(child, base, marks, depth + 1)]


def _blocks(container: Tag, base: str, depth: int = 0) -> list:
    if depth > 40:
        raise ValueError('网页结构过深，暂不支持导入。')
    output, pending = [], []

    def flush():
        if any(part.get('text', '').strip() or part['type'] == 'hardBreak' for part in pending):
            output.append({'type': 'paragraph', 'content': pending.copy()})
        pending.clear()

    for node in container.children:
        if not isinstance(node, Tag):
            pending.extend(_inline(node, base))
            continue
        if node.name in {'div', 'section', 'article', 'main', 'figure', 'figcaption'}:
            flush()
            output.extend(_blocks(node, base, depth + 1))
        elif node.name in {'ul', 'ol'}:
            flush()
            items = []
            for li in node.find_all('li', recursive=False):
                children = _blocks(li, base, depth + 1)
                if not children or children[0]['type'] != 'paragraph':
                    children.insert(0, {'type': 'paragraph'})
                items.append({'type': 'listItem', 'content': children})
            if items:
                attrs = {}
                start = str(node.get('start', '1'))
                if node.name == 'ol' and start.isdigit() and 0 <= int(start) <= 100_000:
                    attrs = {'attrs': {'start': int(start)}}
                output.append({'type': 'orderedList' if node.name == 'ol' else 'bulletList', **attrs, 'content': items})
        elif node.name == 'table':
            flush()
            rows = []
            for row in node.find_all('tr'):
                if row.find_parent('table') is not node:
                    continue
                cells = []
                for cell in row.find_all(['th', 'td'], recursive=False):
                    cells.append({'type': 'tableHeader' if cell.name == 'th' else 'tableCell',
                                  'content': _blocks(cell, base, depth + 1) or [{'type': 'paragraph'}]})
                if cells:
                    rows.append({'type': 'tableRow', 'content': cells})
            if rows:
                width = max(len(row['content']) for row in rows)
                for row in rows:
                    row['content'].extend({'type': 'tableCell', 'content': [{'type': 'paragraph'}]} for _ in range(width - len(row['content'])))
                output.append({'type': 'table', 'content': rows})
        elif node.name == 'blockquote':
            flush()
            children = _blocks(node, base, depth + 1)
            if children:
                output.append({'type': 'blockquote', 'content': children})
        elif node.name in {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre'}:
            flush()
            content = _inline(node, base)
            if content:
                output.append({'type': 'heading', 'attrs': {'level': int(node.name[1])}, 'content': content}
                              if re.fullmatch(r'h[1-6]', node.name) else {'type': 'paragraph', 'content': content})
        elif node.name == 'hr':
            flush()
            output.append({'type': 'horizontalRule'})
        else:
            pending.extend(_inline(node, base))
    flush()
    return output


def parse_article(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    if len(soup.find_all(True)) > 20_000:
        raise ValueError('网页节点过多，暂不支持导入。')
    title_node = soup.select_one('#activity-name') or soup.find('h1')
    meta_title = soup.select_one('meta[property="og:title"]')
    title = _text(title_node.get_text(' ', strip=True)) if title_node else ''
    title = title or (meta_title.get('content', '') if meta_title else '') or (soup.title.get_text() if soup.title else '网页收藏')
    account = soup.select_one('#js_name')
    published = soup.select_one('#publish_time') or soup.find('time')
    metadata = ' · '.join(_text(n.get_text(' ', strip=True)) for n in [account, published] if n)
    root = soup.select_one('#js_content')
    wechat = urlsplit(url).hostname == 'mp.weixin.qq.com'
    if wechat and (root is None or len(root.get_text(strip=True)) < 20):
        raise ValueError('未读取到公众号正文，文章可能已删除或需要验证码；未创建文档。')
    # WeChat initially hides the populated article until its browser scripts run.
    protected = {id(root), *(id(parent) for parent in root.parents)} if wechat and root is not None else set()
    for node in soup.select(SKIP):
        if id(node) in protected:
            continue
        node.decompose()
    for node in soup.select('[hidden], [aria-hidden="true"]'):
        if node.parent and id(node) not in protected:
            node.decompose()
    for node in list(soup.find_all(style=True)):
        if node.parent and id(node) not in protected and re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', node['style'], re.I):
            node.decompose()
    if root is None:
        candidates = soup.select('article, main, [itemprop="articleBody"], .article-content, .entry-content, .post-content')
        root = max(candidates, key=lambda node: len(node.get_text(' ', strip=True)), default=None)
    if root is None:
        candidates = soup.find_all(['div', 'section', 'body'])
        def score(node):
            text = len(node.get_text(' ', strip=True))
            links = sum(len(a.get_text()) for a in node.find_all('a'))
            return text - 2 * links
        root = max(candidates, key=score, default=None)
    if root is None:
        raise ValueError('未找到可导入的文章正文。')
    text = root.get_text(' ', strip=True)
    if re.search(r'captcha|access denied|just a moment|验证后继续|访问过于频繁|环境异常', title, re.I):
        raise ValueError('网站返回了验证或访问限制页面，未创建文档。')
    if len(text) > MAX_TEXT:
        raise ValueError(f'正文共 {len(text):,} 个字符，超过 250,000 字符限制；未创建文档。')
    if len(text) < 40:
        logger.warning('Web import insufficient content: host=%s title=%r selector=%s chars=%d',
                       urlsplit(url).hostname, title[:80], root.get('id') or root.name, len(text))
        raise ValueError(f'仅提取到 {len(text)} 个正文字符，未读取到完整文章（页面标题：{title[:60]}）。'
                         '可能返回了验证页或正文需浏览器加载；请提供该链接以便检查。未创建文档。')
    for heading in list(root.find_all(['h1', 'h2'])):
        if _text(heading.get_text(' ', strip=True)) == title:
            heading.decompose()
            break
    blocks = _blocks(root, url)
    if not blocks:
        raise ValueError('未找到可导入的文章正文。')
    source = {'type': 'paragraph', 'content': [{'type': 'text', 'text': '原文来源：'}, {
        'type': 'text', 'text': url, 'marks': [{'type': 'link', 'attrs': {'href': url, 'target': '_blank', 'rel': 'noopener noreferrer'}}],
    }]}
    prefix = [{'type': 'heading', 'attrs': {'level': 1}, 'content': [{'type': 'text', 'text': title}]}, source]
    if metadata:
        prefix.append({'type': 'paragraph', 'content': [{'type': 'text', 'text': metadata}]})
    return {'type': 'doc', 'content': [*prefix, {'type': 'horizontalRule'}, *blocks]}


def import_web_document(url: str) -> dict:
    html, final_url = fetch_html(url)
    return parse_article(html, final_url)

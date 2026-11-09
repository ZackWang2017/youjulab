"""Draw.io asset validation and deterministic node/edge conversion; no browser required."""
from __future__ import annotations

import base64
import math
import re
import zlib
from html import unescape
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

MAX_XML_BYTES = 2 * 1024 * 1024
MAX_CELLS = 5000
MAX_PAGES = 20
TAGS = {'mxfile', 'diagram', 'mxGraphModel', 'root', 'mxCell', 'mxGeometry', 'mxPoint',
        'mxRectangle', 'Array', 'Object', 'object', 'UserObject'}
HTML_TAGS = {'b', 'strong', 'i', 'em', 'u', 's', 'strike', 'span', 'div', 'p', 'br',
             'font', 'small', 'sub', 'sup', 'ul', 'ol', 'li', 'a', 'table', 'tbody', 'tr', 'td', 'th'}


def _link(value: str):
    compact = re.sub(r'\s+', '', value)
    parts = urlsplit(compact)
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
        raise ValueError('绘图链接仅允许 HTTP/HTTPS 地址。')


def _label(value: str):
    for _ in range(4):
        decoded = unescape(value)
        if decoded == value:
            break
        value = decoded
    if re.search(r'javascript\s*:|vbscript\s*:|data\s*:|url\s*\(|expression\s*\(', value, re.I):
        raise ValueError('绘图标签包含不安全的内容。')
    soup = BeautifulSoup(value, 'html.parser')
    for tag in soup.find_all(True):
        if tag.name not in HTML_TAGS:
            raise ValueError(f'绘图标签不支持 HTML 元素 {tag.name}。')
        for key, val in tag.attrs.items():
            if key.lower() not in {'style', 'color', 'face', 'size', 'align', 'href', 'target', 'rel', 'colspan', 'rowspan', 'title'}:
                raise ValueError(f'绘图标签包含不安全的 HTML 属性 {key}。')
            if key == 'href':
                _link(str(val))


def _parse(xml: str):
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)|<\?', xml, re.I):
        # The optional XML declaration is metadata, not an executable processing instruction.
        xml = re.sub(r'^\s*<\?xml\s+[^?]*\?>', '', xml, count=1)
        if re.search(r'<!\s*(?:DOCTYPE|ENTITY)|<\?', xml, re.I):
            raise ValueError('绘图 XML 不允许 DTD、实体或处理指令。')
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError('绘图 XML 格式错误。') from exc


def _number(value, label: str, *, positive=False):
    if isinstance(value, bool):
        raise ValueError(f'{label}必须是有效数字。')
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label}必须是有效数字。') from exc
    if not math.isfinite(number) or abs(number) > 1_000_000 or (positive and number <= 0):
        raise ValueError(f'{label}超出允许范围。')
    return number


def validate_xml(xml: str) -> str:
    if not isinstance(xml, str) or not xml.strip():
        raise ValueError('xml 不能为空。')
    if len(xml.encode('utf-8')) > MAX_XML_BYTES:
        raise ValueError('绘图 XML 不能超过 2 MB。')
    root = _parse(xml)
    if root.tag == 'mxGraphModel':
        models = [root]
    elif root.tag == 'mxfile':
        pages = list(root)
        if not pages or len(pages) > MAX_PAGES or any(p.tag != 'diagram' for p in pages):
            raise ValueError('mxfile 应包含 1 至 20 个 diagram 页面。')
        models = []
        for page in pages:
            if len(page) == 0 and (page.text or '').strip():
                try:
                    packed = base64.b64decode(page.text.strip(), validate=True)
                    decoder = zlib.decompressobj(-15)
                    raw = decoder.decompress(packed, MAX_XML_BYTES + 1)
                    if len(raw) > MAX_XML_BYTES or not decoder.eof or decoder.unused_data:
                        raise ValueError('压缩页面超过限制或格式错误。')
                    model = _parse(unquote(raw.decode('utf-8')))
                except (ValueError, zlib.error, UnicodeError) as exc:
                    raise ValueError('压缩绘图页面无效或超过限制。') from exc
                page.text = None
                page.append(model)
            if len(page) != 1 or page[0].tag != 'mxGraphModel':
                raise ValueError('每个 diagram 应包含一个 mxGraphModel。')
            models.append(page[0])
    else:
        raise ValueError('XML 根节点必须是 mxfile 或 mxGraphModel。')
    count = 0
    stack = [(root, 0)]
    while stack:
        element, depth = stack.pop()
        count += 1
        if depth > 60 or count > 30_000:
            raise ValueError('绘图结构过深或节点过多。')
        if element.tag not in TAGS:
            raise ValueError(f'绘图不支持 XML 元素 {element.tag}。')
        for key, value in element.attrib.items():
            if key.lower().startswith('on') or key.lower() in {'src', 'href', 'xlink:href'}:
                raise ValueError(f'绘图包含不安全的属性 {key}。')
            if key in {'value', 'label', 'tooltip'} or '<' in unescape(value):
                _label(value)
            if key == 'link' and value:
                _link(value)
            if key == 'style' and re.search(r'image\s*=|stencil\s*(?:=|\()|url\s*\(|expression\s*\(|javascript\s*:', value, re.I):
                raise ValueError('首版不支持图片、内嵌 stencil 或外部资源样式。')
        stack.extend((child, depth + 1) for child in element)
    total = 0
    for model in models:
        graph_root = model.find('root')
        if graph_root is None or len(model.findall('root')) != 1:
            raise ValueError('mxGraphModel 必须包含一个 root。')
        ids, cells = {}, []
        for item in graph_root:
            cell = item if item.tag == 'mxCell' else item.find('mxCell')
            if cell is None or (item.tag != 'mxCell' and len(item.findall('mxCell')) != 1):
                raise ValueError('root 下只支持 mxCell 或含 mxCell 的对象。')
            cell_id = item.get('id') or cell.get('id')
            if not cell_id or cell_id in ids:
                raise ValueError('绘图单页内的节点 ID 必须非空且唯一。')
            ids[cell_id] = cell
            cells.append((cell_id, cell))
        total += len(cells)
        if total > MAX_CELLS:
            raise ValueError('绘图最多支持 5000 个单元（含节点、连线与图层）。')
        if '0' not in ids or '1' not in ids or ids['1'].get('parent') != '0':
            raise ValueError('绘图缺少根单元 0 或默认图层 1。')
        for cell_id, cell in cells:
            for key in ('parent', 'source', 'target'):
                ref = cell.get(key)
                if ref is not None and (ref not in ids or ref == cell_id):
                    raise ValueError(f'节点 {cell_id} 的 {key} 引用无效。')
            if cell_id != '0' and not cell.get('parent'):
                raise ValueError(f'节点 {cell_id} 缺少 parent。')
            seen = {cell_id}
            parent = cell.get('parent')
            while parent:
                if parent in seen:
                    raise ValueError('绘图父子关系不能形成循环。')
                seen.add(parent)
                parent = ids[parent].get('parent')
            geometry = cell.find('mxGeometry')
            if geometry is not None:
                for key in ('x', 'y', 'width', 'height'):
                    if key in geometry.attrib:
                        value = _number(geometry.get(key), f'geometry.{key}')
                        if key in {'width', 'height'} and value < 0:
                            raise ValueError('geometry 尺寸不能为负数。')
    result = ET.tostring(root, encoding='unicode')
    if len(result.encode('utf-8')) > MAX_XML_BYTES:
        raise ValueError('展开后的绘图 XML 不能超过 2 MB。')
    return result


def diagram_content(content: dict) -> dict:
    if not isinstance(content, dict) or content.get('format') != 'drawio-v1':
        raise ValueError('绘图内容必须是 drawio-v1 对象。')
    return {**content, 'xml': validate_xml(content.get('xml'))}


def build_diagram(graph: dict) -> dict:
    if not isinstance(graph, dict) or set(graph) - {'nodes', 'edges', 'layout'}:
        raise ValueError('graph 仅支持 nodes、edges、layout。')
    nodes, edges = graph.get('nodes', []), graph.get('edges', [])
    if not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) + len(edges) + 2 > MAX_CELLS:
        raise ValueError('nodes、edges 必须为数组，总量不能超过 4998。')
    layout = graph.get('layout') or {}
    if not isinstance(layout, dict) or layout.get('direction', 'left-to-right') not in {'left-to-right', 'top-to-bottom'}:
        raise ValueError('layout.direction 应为 left-to-right 或 top-to-bottom。')
    model = ET.Element('mxGraphModel', grid='1', gridSize='10', page='1', pageWidth='1169', pageHeight='827')
    root = ET.SubElement(model, 'root')
    ET.SubElement(root, 'mxCell', id='0')
    ET.SubElement(root, 'mxCell', id='1', parent='0')
    ids = {'0', '1'}
    vertices = set()
    def identifier(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 160 or value in ids:
            raise ValueError('节点和连线 ID 必须非空、唯一，且不能使用 0、1。')
        ids.add(value)
        return value
    def label(value):
        if not isinstance(value, str) or len(value) > 5000:
            raise ValueError('text 必须是最多 5000 字符的字符串。')
        return value
    shapes = {'rectangle': 'rounded=0;', 'rounded': 'rounded=1;', 'ellipse': 'ellipse;',
              'diamond': 'rhombus;', 'text': 'text;strokeColor=none;fillColor=none;'}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or set(node) - {'id', 'text', 'shape', 'x', 'y', 'width', 'height', 'fill', 'stroke'}:
            raise ValueError('节点字段无效。')
        node_id = identifier(node.get('id'))
        vertices.add(node_id)
        shape = node.get('shape', 'rounded')
        if shape not in shapes:
            raise ValueError('shape 仅支持 rectangle、rounded、ellipse、diamond、text。')
        style = shapes[shape] + 'whiteSpace=wrap;html=0;'
        for field, key, default in [('fill', 'fillColor', '#E9F3EE'), ('stroke', 'strokeColor', '#679581')]:
            color = node.get(field, default)
            if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
                raise ValueError('fill、stroke 必须是 #RRGGBB 颜色。')
            style += f'{key}={color};'
        cell = ET.SubElement(root, 'mxCell', id=node_id, value=label(node.get('text', '')), style=style, vertex='1', parent='1')
        horizontal = layout.get('direction', 'left-to-right') == 'left-to-right'
        x, y = ((index % 4) * 240 + 60, (index // 4) * 160 + 60) if horizontal else ((index // 6) * 240 + 60, (index % 6) * 160 + 60)
        ET.SubElement(cell, 'mxGeometry', **{key: str(_number(node.get(key, default), key, positive=key in {'width', 'height'}))
                                         for key, default in [('x', x), ('y', y), ('width', 180), ('height', 70)]}, **{'as': 'geometry'})
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict) or set(edge) - {'id', 'source', 'target', 'text'}:
            raise ValueError('连线字段无效。')
        edge_id = identifier(edge.get('id', f'edge-{index + 1}'))
        if edge.get('source') not in vertices or edge.get('target') not in vertices:
            raise ValueError('连线 source、target 必须是现有节点 ID。')
        cell = ET.SubElement(root, 'mxCell', id=edge_id, value=label(edge.get('text', '')), source=edge['source'], target=edge['target'],
                             edge='1', parent='1', style='edgeStyle=orthogonalEdgeStyle;endArrow=classic;html=0;')
        ET.SubElement(cell, 'mxGeometry', relative='1', **{'as': 'geometry'})
    return {'format': 'drawio-v1', 'xml': validate_xml(ET.tostring(model, encoding='unicode'))}


def diagram_input(graph: dict | None, xml: str) -> dict:
    if graph is not None and xml:
        raise ValueError('graph 与 xml 只能提供一种。')
    return {'format': 'drawio-v1', 'xml': validate_xml(xml)} if xml else build_diagram(graph if graph is not None else {})


def describe_diagram(content: dict) -> list[dict]:
    root = _parse(diagram_content(content)['xml'])
    pages = [('page-1', 'Page-1', root)] if root.tag == 'mxGraphModel' else [
        (page.get('id') or f'page-{index+1}', page.get('name') or f'Page-{index+1}', page[0]) for index, page in enumerate(root)]
    result = []
    for page_id, name, model in pages:
        nodes, edges = [], []
        for item in model.find('root'):
            cell = item if item.tag == 'mxCell' else item.find('mxCell')
            if cell is None or not (cell.get('vertex') == '1' or cell.get('edge') == '1'):
                continue
            entry = {'id': item.get('id') or cell.get('id'), 'text': item.get('label') or cell.get('value', ''),
                     'parent': cell.get('parent'), 'style': cell.get('style', '')}
            geometry = cell.find('mxGeometry')
            if geometry is not None:
                entry['geometry'] = dict(geometry.attrib)
            if cell.get('edge') == '1':
                entry.update(source=cell.get('source'), target=cell.get('target'))
                edges.append(entry)
            else:
                nodes.append(entry)
        result.append({'id': page_id, 'name': name, 'nodes': nodes, 'edges': edges})
    return result

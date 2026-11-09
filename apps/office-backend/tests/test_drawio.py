"""Draw.io format, authenticated MCP/REST, version conflicts, and local persistence."""
import base64
import concurrent.futures
import json
import os
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from drawio import MAX_CELLS, build_diagram, describe_diagram, diagram_input, validate_xml
from server import create_app
from skills import render_skill
from sqlite_store import SqliteStore
from store import MemoryStore, default_diagram

GRAPH = {'nodes': [{'id': 'revenue', 'text': '营业收入 & 利润', 'x': 40, 'y': 80},
                   {'id': 'profit', 'text': '经营利润', 'shape': 'diamond'}],
         'edges': [{'source': 'revenue', 'target': 'profit', 'text': '减去经营成本'}]}


class DrawioFormatTests(unittest.TestCase):
    def test_graph_has_editable_cells_geometry_and_stable_ids(self):
        content = build_diagram(GRAPH)
        page = describe_diagram(content)[0]
        self.assertEqual(len(page['nodes']), 2)
        self.assertEqual(page['nodes'][0]['id'], 'revenue')
        self.assertEqual(page['nodes'][0]['text'], '营业收入 & 利润')
        self.assertEqual(float(page['nodes'][0]['geometry']['x']), 40)
        self.assertEqual(page['edges'][0]['target'], 'profit')
        self.assertIn('rhombus;', page['nodes'][1]['style'])

    def test_default_frontend_xml_validates(self):
        self.assertIn('mxGraphModel', validate_xml(default_diagram()['xml']))

    def test_graph_inputs_are_exclusive(self):
        with self.assertRaises(ValueError):
            diagram_input(GRAPH, default_diagram()['xml'])

    def test_invalid_graph_rejected(self):
        for graph in [
            {'nodes': [{'id': '0'}]}, {'nodes': [{'id': 'a'}, {'id': 'a'}]},
            {'nodes': [], 'edges': [{'source': 'missing', 'target': 'missing'}]},
            {'nodes': [{'id': 'a', 'shape': 'javascript'}]},
            {'nodes': [{'id': 'a', 'x': float('nan')}]},
            {'nodes': [{'id': 'a', 'width': -10}]},
            {'nodes': [{'id': 'a', 'fill': 'red;image=https://evil'}]},
            {'nodes': [{'id': str(i)} for i in range(MAX_CELLS)]},
        ]:
            with self.subTest(graph=str(graph)[:80]), self.assertRaises((ValueError, TypeError)):
                build_diagram(graph)

    def test_unsafe_xml_and_labels_rejected(self):
        original = default_diagram()['xml']
        for xml in [
            '<!DOCTYPE mxGraphModel [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + original,
            original.replace('value="公告"', 'value="&lt;img src=&quot;https://evil&quot; onerror=&quot;alert(1)&quot;&gt;"'),
            original.replace('value="公告"', 'value="&lt;script&gt;alert(1)&lt;/script&gt;"'),
            original.replace('vertex="1"', 'onclick="alert(1)" vertex="1"', 1),
            original.replace('rounded=1;', 'image=https://evil;'),
            original.replace('rounded=1;', 'stencil=encoded;'),
            original.replace('rounded=1;', 'shape=stencil(encoded);'),
            original.replace('vertex="1"', 'link="javascript:alert(1)" vertex="1"', 1),
            original.replace('value="公告"', 'value="&amp;lt;script&amp;gt;alert(1)&amp;lt;/script&amp;gt;"'),
        ]:
            with self.subTest(xml=xml[:100]), self.assertRaises(ValueError):
                validate_xml(xml)

    def test_broken_references_duplicate_ids_and_parent_cycles_rejected(self):
        xml = default_diagram()['xml']
        for bad in [xml.replace('source="2"', 'source="missing"'),
                    xml.replace('id="3"', 'id="2"'),
                    xml.replace('id="1" parent="0"', 'id="1" parent="2"')]:
            with self.assertRaises(ValueError):
                validate_xml(bad)

    def test_compressed_and_multi_page_preserve_groups_and_metadata(self):
        model = default_diagram()['xml']
        compressor = zlib.compressobj(wbits=-15)
        packed = base64.b64encode(compressor.compress(quote(model).encode()) + compressor.flush()).decode()
        raw = f'<mxfile><diagram id="p1" name="第一页">{packed}</diagram><diagram id="p2" name="第二页">{model}</diagram></mxfile>'
        normalized = validate_xml(raw)
        self.assertEqual(len(ET.fromstring(normalized)), 2)
        pages = describe_diagram({'format': 'drawio-v1', 'xml': normalized})
        self.assertEqual([p['name'] for p in pages], ['第一页', '第二页'])
        self.assertEqual(len(pages[1]['nodes']), 3)

    def test_user_object_ids_and_safe_html_round_trip(self):
        root = ET.fromstring(default_diagram()['xml'])
        layer = root.find('root')
        cell = layer.find("mxCell[@id='2']")
        layer.remove(cell)
        cell.attrib.pop('id')
        wrapper = ET.SubElement(layer, 'UserObject', id='2', label='<b>营业收入</b>', custom='keep-me')
        wrapper.append(cell)
        xml = validate_xml(ET.tostring(root, encoding='unicode'))
        self.assertIn('keep-me', xml)
        self.assertIn('<b>营业收入</b>', str(describe_diagram({'format': 'drawio-v1', 'xml': xml})))

    def test_compressed_bomb_is_rejected(self):
        compressor = zlib.compressobj(wbits=-15)
        packed = base64.b64encode(compressor.compress(b'x' * (2 * 1024 * 1024 + 1)) + compressor.flush()).decode()
        with self.assertRaises(ValueError):
            validate_xml('<mxfile><diagram>' + packed + '</diagram></mxfile>')


class DiagramApiTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.client = TestClient(create_app(self.store), base_url='http://127.0.0.1')
        self.auth = self.client.post('/auth/register', json={'email': 'diagram@example.com', 'password': 'secret1'}).json()
        self.jwt = {'Authorization': 'Bearer ' + self.auth['token']}
        token = self.client.get('/auth/access-token', headers=self.jwt).json()['access_token']
        self.headers = {'Authorization': 'Bearer ' + token}
        self.project = self.client.get('/projects', headers=self.jwt).json()['projects'][0]

    def create(self, **kwargs):
        response = self.client.post('/api/diagrams/create', headers=self.headers, json={
            'project_name': self.project['name'], 'diagram_name': '利润分析', 'graph': GRAPH, **kwargs,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_create_read_write_and_ui_asset_round_trip(self):
        created = self.create()
        self.assertEqual(created['revision'], 0)
        read = self.client.post('/api/diagrams/read', headers=self.headers, json={
            'asset_id': created['asset_id'], 'include_xml': True,
        }).json()
        self.assertEqual(read['node_count'], 2)
        response = self.client.post('/api/diagrams/write', headers=self.headers, json={
            'asset_id': created['asset_id'], 'expected_revision': read['revision'],
            'xml': read['xml'].replace('经营利润', '净利润'),
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['revision'], 1)
        asset = self.client.get('/assets/' + created['asset_id'], headers=self.jwt).json()
        self.assertEqual(asset['content']['format'], 'drawio-v1')
        self.assertIn('净利润', asset['content']['xml'])

    def test_user_visible_asset_is_accessible_across_own_projects(self):
        created = self.create(visibility='user')
        self.assertEqual(created['visibility'], 'user')
        self.assertIsNone(created['project_id'])
        other = self.client.post('/projects', headers=self.jwt, json={'name': '另一个项目'}).json()
        response = self.client.post('/api/diagrams/read', headers=self.headers, json={
            'project_name': other['name'], 'diagram_name': '利润分析',
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['asset_id'], created['asset_id'])

    def test_duplicate_and_stale_revision_do_not_overwrite(self):
        created = self.create()
        duplicate = self.client.post('/api/diagrams/create', headers=self.headers, json={
            'project_name': self.project['name'], 'diagram_name': '利润分析',
        })
        self.assertEqual(duplicate.status_code, 409)
        args = {'asset_id': created['asset_id'], 'expected_revision': 0, 'graph': {'nodes': [{'id': 'new', 'text': '修改'}]}}
        first = self.client.post('/api/diagrams/write', headers=self.headers, json=args)
        self.assertEqual(first.status_code, 200, first.text)
        stale = self.client.post('/api/diagrams/write', headers=self.headers, json={**args, 'graph': GRAPH})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.post('/api/diagrams/read', headers=self.headers, json={'asset_id': created['asset_id']}).json()['node_count'], 1)

    def test_bad_inputs_do_not_create_assets(self):
        count = len(self.store._assets)
        response = self.client.post('/api/diagrams/create', headers=self.headers, json={
            'project_name': '不应创建的项目', 'diagram_name': '错误绘图', 'graph': {'nodes': [{'id': '0'}]},
        })
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(len(self.store._assets), count)
        self.assertFalse(any(p['name'] == '不应创建的项目' for p in self.store.projects(self.auth['user']['id'])))

    def test_missing_read_and_write_do_not_create(self):
        for action, extra in [('read', {}), ('write', {'expected_revision': 0, 'graph': GRAPH})]:
            response = self.client.post('/api/diagrams/' + action, headers=self.headers, json={'asset_id': 'missing', **extra})
            self.assertEqual(response.status_code, 404, response.text)

    def test_missing_wrong_and_other_user_tokens_are_rejected(self):
        created = self.create()
        for path in ['/api/diagrams/read', '/mcp/diagrams']:
            self.assertEqual(self.client.post(path, json={}).status_code, 401)
            self.assertEqual(self.client.post(path, headers=self.jwt, json={}).status_code, 401)
        other = self.client.post('/auth/register', json={'email': 'other@example.com', 'password': 'secret1'}).json()
        token = self.client.get('/auth/access-token', headers={'Authorization': 'Bearer ' + other['token']}).json()['access_token']
        response = self.client.post('/api/diagrams/read', headers={'Authorization': 'Bearer ' + token}, json={'asset_id': created['asset_id']})
        self.assertEqual(response.status_code, 404)

    def test_non_diagram_and_wrong_project_are_rejected(self):
        response = self.client.post('/api/diagrams/read', headers=self.headers, json={'asset_id': self.auth['user']['last_assets']['sheet']})
        self.assertEqual(response.status_code, 400)
        created = self.create()
        other = self.client.post('/projects', headers=self.jwt, json={'name': '隔离项目'}).json()
        response = self.client.post('/api/diagrams/read', headers=self.headers, json={'asset_id': created['asset_id'], 'project_name': other['name']})
        self.assertEqual(response.status_code, 404)

    def test_editor_save_uses_same_xml_validation(self):
        created = self.create()
        response = self.client.put('/assets/' + created['asset_id'], headers=self.jwt, json={
            'revision': 0, 'content': {'format': 'drawio-v1', 'xml': '<bad/>'},
        })
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.store.asset(self.auth['user']['id'], created['asset_id'])['revision'], 0)

    def test_stateless_mcp_tools_list_and_call(self):
        headers = {**self.headers, 'Accept': 'application/json, text/event-stream'}
        def rpc(method, params, request_id=1):
            response = self.client.post('/mcp/diagrams', headers=headers, json={
                'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params,
            })
            self.assertEqual(response.status_code, 200, response.text)
            if response.headers.get('content-type', '').startswith('text/event-stream'):
                return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))
            return response.json()
        with self.client:
            initialized = rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}})
            self.assertIn('result', initialized)
            tools = rpc('tools/list', {})['result']['tools']
            self.assertEqual({t['name'] for t in tools}, {'create_diagram', 'read_diagram', 'write_diagram'})
            write = next(t for t in tools if t['name'] == 'write_diagram')
            self.assertIn('expected_revision', write['inputSchema']['required'])
            result = rpc('tools/call', {'name': 'create_diagram', 'arguments': {
                'project_name': self.project['name'], 'diagram_name': 'MCP 新绘图', 'graph': GRAPH,
            }})['result']
            self.assertFalse(result.get('isError'), result)
            saved = result.get('structuredContent') or json.loads(result['content'][0]['text'])
            self.assertEqual(saved['node_count'], 2)
            self.assertEqual(self.store.asset(self.auth['user']['id'], saved['asset_id'])['kind'], 'diagram')
            read = rpc('tools/call', {'name': 'read_diagram', 'arguments': {'asset_id': saved['asset_id'], 'include_xml': True}})['result']
            document = read.get('structuredContent') or json.loads(read['content'][0]['text'])
            self.assertIn('xml', document)
            written = rpc('tools/call', {'name': 'write_diagram', 'arguments': {
                'asset_id': saved['asset_id'], 'expected_revision': document['revision'], 'xml': document['xml'],
            }})['result']
            self.assertFalse(written.get('isError'), written)
            # Reusing an HTTP connection must not reuse another user's authentication context.
            other = self.client.post('/auth/register', json={'email': 'mcp-other@example.com', 'password': 'secret1'}).json()
            token = self.client.get('/auth/access-token', headers={'Authorization': 'Bearer ' + other['token']}).json()['access_token']
            headers['Authorization'] = 'Bearer ' + token
            denied = rpc('tools/call', {'name': 'read_diagram', 'arguments': {'asset_id': saved['asset_id']}})['result']
            self.assertTrue(denied.get('isError'), denied)

    def test_configured_public_mcp_host_and_origin_validation(self):
        with patch.dict(os.environ, {'OFFICE_PUBLIC_BASE_URL': 'https://office.example.test'}):
            with TestClient(create_app(self.store), base_url='https://office.example.test') as client:
                headers = {**self.headers, 'Accept': 'application/json, text/event-stream'}
                body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}
                response = client.post('/mcp/diagrams', headers=headers, json=body)
                self.assertEqual(response.status_code, 200, response.text)
                denied = client.post('/mcp/diagrams', headers={**headers, 'Origin': 'https://evil.example'}, json=body)
                self.assertEqual(denied.status_code, 403)

    def test_downloaded_skill_is_self_contained(self):
        text, filename = render_skill('office', 'test-token')
        self.assertIn('/mcp/diagrams', text)
        self.assertIn('expected_revision', text)
        self.assertNotIn('__ACCESS_TOKEN__', text)
        self.assertNotIn('__OFFICE_BASE_URL__', text)
        response = self.client.get('/auth/skills/office', headers=self.jwt)
        self.assertEqual(response.status_code, 200)
        self.assertIn('/mcp/diagrams', response.text)
        self.assertIn('create_diagram', response.text)
        self.assertIn('expected_revision', response.text)


class DiagramPersistenceTests(unittest.TestCase):
    def test_sqlite_persists_and_compare_and_swap_has_one_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'office.sqlite'
            store = SqliteStore(path)
            user = store.create_user('persist@example.com', 'secret1')
            project = store.create_project(user['id'], '研究')
            asset = store.create_asset(user['id'], project['id'], 'diagram', '绘图', build_diagram(GRAPH))
            def write(index):
                return store.update_asset(user['id'], asset['id'], expected_revision=0, revision=1, content=build_diagram({'nodes': [{'id': f'n{index}'}]}))
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(write, [1, 2]))
            self.assertEqual(sum(result is not None for result in results), 1)
            store.close()
            restored = SqliteStore(path)
            self.assertEqual(restored.asset(user['id'], asset['id'])['revision'], 1)
            self.assertEqual(len(describe_diagram(restored.asset(user['id'], asset['id'])['content'])[0]['nodes']), 1)
            restored.close()


if __name__ == '__main__':
    unittest.main()

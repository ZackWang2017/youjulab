import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from store import MemoryStore
from server import create_app


class AssetCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'OFFICE_SQLITE_PATH': str(Path(self.temp.name) / 'office.sqlite'),
                                     'OFFICE_FILE_ROOT': self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.store = MemoryStore()
        self.client = TestClient(create_app(self.store))
        self.addCleanup(self.client.close)
        auth = self.client.post('/auth/register', json={'email': 'copy@example.com', 'password': 'secret1'}).json()
        self.user = auth['user']['id']
        self.project = auth['user']['last_project_id']
        self.headers = {'Authorization': f"Bearer {auth['token']}"}

    def test_sheet_copy_rebinds_identity_and_preserves_original(self):
        source = self.store.create_asset(self.user, self.project, 'sheet', 'Sheet', {'id': 'old'})
        draft = {'id': source['id'], 'sheets': {'s1': {'cellData': {'0': {'0': {'v': 'local'}}}}}}
        for number in range(2):
            r = self.client.post(f"/assets/{source['id']}/copy", json={'content': draft}, headers=self.headers)
            self.assertEqual(r.status_code, 200, r.text)
            copy = r.json()
            self.assertNotEqual(copy['id'], source['id'])
            self.assertEqual(copy['content']['id'], copy['id'])
            self.assertEqual(copy['project_id'], self.project)
            self.assertIn('副本', copy['name'])
        self.assertEqual(self.store.asset(self.user, source['id'])['content'], {'id': 'old'})

    def test_stale_revision_is_rejected_for_documents(self):
        a = self.store.create_asset(self.user, self.project, 'document', 'Doc', {'type': 'doc', 'content': []})
        url = f"/assets/{a['id']}"
        body = {'content': {'type': 'doc', 'content': []}, 'revision': 0}
        self.assertEqual(self.client.put(url, json=body, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.put(url, json=body, headers=self.headers).status_code, 409)

    def test_encrypted_sheet_copy_preserves_encryption(self):
        from sheet_crypto import encrypt_sheet, decrypt_sheet
        a = self.store.create_asset(self.user, self.project, 'sheet', 'Private', {})
        self.store.update_asset(self.user, a['id'], content=encrypt_sheet({'id': a['id'], 'sheets': {}}, 'secret', asset_id=a['id']))
        draft = {'id': a['id'], 'sheets': {}}
        url = f"/assets/{a['id']}/copy"
        self.assertEqual(self.client.post(url, json={'content': draft}, headers=self.headers).status_code, 403)
        result = self.client.post(url, json={'content': draft}, headers={**self.headers, 'X-File-Password': 'secret'})
        self.assertEqual(result.status_code, 200, result.text)
        copy = self.store.asset(self.user, result.json()['id'])
        self.assertEqual(decrypt_sheet(copy['content'], 'secret', asset_id=copy['id'])['id'], copy['id'])

    def test_unauthorized_copy_and_validation_cleanup(self):
        a = self.store.create_asset(self.user, self.project, 'diagram', 'Diagram', {})
        url = f"/assets/{a['id']}/copy"
        self.assertEqual(self.client.post(url, json={'content': {}}).status_code, 401)
        before = len(self.store.assets(self.user, self.project, 'diagram'))
        self.assertEqual(self.client.post(url, json={'content': {'format': 'invalid'}}, headers=self.headers).status_code, 400)
        self.assertEqual(len(self.store.assets(self.user, self.project, 'diagram')), before)

    def test_pdf_copy_keeps_local_notes_and_independent_file(self):
        p = Path(self.temp.name) / 'original.pdf'
        p.write_bytes(b'%PDF-1.4\n%%EOF')
        a = self.store.create_asset(self.user, self.project, 'pdf', 'Report', {})
        self.store.update_asset(self.user, a['id'], file_path=str(p), filename='report.pdf')
        content = {'notebook': {'type': 'doc', 'content': []}}
        r = self.client.post(f"/assets/{a['id']}/copy", json={'content': content}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        copied = self.store.asset(self.user, r.json()['id'])
        self.assertNotEqual(copied['file_path'], str(p))
        self.assertEqual(Path(copied['file_path']).read_bytes(), p.read_bytes())
        self.assertEqual(copied['content'], content)

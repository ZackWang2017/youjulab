import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from server import create_app
from store import MemoryStore
from sheet_identity import sheet_content_for_asset
from agent_tools.store_client import StoreClient
from agent_tools.errors import OfficeError
from sheet_crypto import encrypt_sheet


class SheetIdentityTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.client = TestClient(create_app(self.store))
        auth = self.client.post('/auth/register', json={'email': 'identity@test.com', 'password': 'secret1'}).json()
        self.headers = {'Authorization': 'Bearer ' + auth['token']}
        self.owner = auth['user']['id']
        self.first = self.store.create_asset(self.owner, None, 'sheet', 'first', {})
        self.second = self.store.create_asset(self.owner, None, 'sheet', 'second', {})

    def save(self, asset, content):
        return self.client.put('/assets/' + asset['id'], headers=self.headers,
                               json={'revision': asset['revision'], 'content': content})

    def test_foreign_workbook_rejected_without_mutation(self):
        result = self.save(self.second, {'id': self.first['id'], 'sheets': {}})
        self.assertEqual(result.status_code, 409, result.text)
        for original in (self.first, self.second):
            current = self.store.asset(self.owner, original['id'])
            self.assertEqual(current['revision'], original['revision'])
            self.assertEqual(current['content'], {})

    def test_missing_id_bound_and_matching_snapshot_saved(self):
        result = self.save(self.first, {'sheets': {}})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['content']['id'], self.first['id'])

    def test_no_silent_repair_of_already_corrupted_snapshot(self):
        corrupt = {'id': self.first['id'], 'sheets': {}}
        self.store.update_asset(self.owner, self.second['id'], content=corrupt)
        result = self.save(self.second, corrupt)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.store.asset(self.owner, self.second['id'])['content'], corrupt)

    def test_normalization_does_not_mutate_caller(self):
        content = {'sheets': {}}
        self.assertEqual(sheet_content_for_asset('a', content)['id'], 'a')
        self.assertNotIn('id', content)

    def test_agent_rejects_foreign_snapshot(self):
        client = StoreClient(self.store, {'id': self.owner})
        with self.assertRaises(OfficeError):
            client.save_asset(self.second['id'], {'id': self.first['id'], 'sheets': {}}, self.second['revision'])
        self.assertEqual(self.store.asset(self.owner, self.second['id'])['content'], {})

    def test_encrypted_target_rejects_foreign_plaintext(self):
        envelope = encrypt_sheet({'id': self.second['id'], 'sheets': {}}, 'secret1', asset_id=self.second['id'])
        self.store.update_asset(self.owner, self.second['id'], content=envelope)
        result = self.client.put('/assets/' + self.second['id'], headers=self.headers,
                                 json={'revision': self.second['revision'], 'password': 'secret1',
                                       'content': {'id': self.first['id'], 'sheets': {}}})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.store.asset(self.owner, self.second['id'])['content'], envelope)


if __name__ == '__main__':
    unittest.main()

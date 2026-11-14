import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from information.migrate_postgres import import_data
from information.repository.information_repository import INFORMATION_TABLES, initialize_schema


class InformationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name)/'office.sqlite'
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE users (id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            conn.execute('CREATE TABLE projects (id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            conn.execute('CREATE TABLE assets (id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            for uid in ('alice', 'bob'):
                conn.execute('INSERT INTO users VALUES (?,?)', (uid, json.dumps({'id': uid, 'email': uid+'@example.com'})))
                conn.execute('INSERT INTO projects VALUES (?,?)', (uid+'-project', json.dumps({'id': uid+'-project', 'owner_id': uid, 'name': uid})))
            initialize_schema(conn)
            conn.execute("INSERT INTO information_groups (owner_id,name,kind) VALUES ('alice','收藏夹','favorites')")
        self.accounts = [{'id': 'remote-alice', 'email': 'alice@example.com'}, {'id': 'remote-bob', 'email': 'bob@example.com'}]
        self.data = {
            'information_groups': [
                {'id': 1, 'owner_id': 'remote-bob', 'name': '收藏夹', 'kind': 'favorites'},
                {'id': 3, 'owner_id': 'remote-alice', 'name': '收藏夹', 'kind': 'favorites'},
                {'id': 4, 'owner_id': 'remote-alice', 'name': 'API', 'kind': 'finance_api', 'config': '{"keywords":"Tencent"}'},
            ],
            'information_items': [
                {'id': 1, 'owner_id': 'remote-bob', 'group_id': 1, 'source_kind': 'wechat', 'source_name': 'source', 'title': 'Bob', 'fetched_date': '2026-11-11'},
                {'id': 2, 'owner_id': 'remote-alice', 'group_id': 4, 'source_kind': 'finance_api', 'source_name': 'source', 'title': 'Alice', 'fetched_date': '2026-11-11', 'favorited': True},
            ],
            'information_reviews': [{'id': 1, 'owner_id': 'remote-alice', 'group_id': 4, 'review_date': '2026-11-11', 'content': 'Review',
                'item_refs': json.dumps([{'id': 2, 'title': 'Alice', 'url': 'https://example.com'}, {'id': 999, 'title': 'Deleted'}])}],
        }

    def test_preview_is_readonly(self):
        before = self.db.read_bytes()
        result = import_data(self.db, self.accounts, self.data)
        self.assertEqual(result['mode'], 'dry-run')
        self.assertEqual(result['tables']['information_groups'], {'inserted': 2, 'existing': 1, 'unmatched': 0})
        self.assertEqual(before, self.db.read_bytes())
        self.assertFalse((self.db.parent/'migration-backups').exists())

    def test_remaps_ids_accounts_refs_and_is_idempotent(self):
        result = import_data(self.db, self.accounts, self.data, apply=True)
        self.assertTrue(Path(result['backup']).is_file())
        with sqlite3.connect(self.db) as conn:
            groups = conn.execute('SELECT owner_id,count(*) FROM information_groups GROUP BY owner_id ORDER BY owner_id').fetchall()
            self.assertEqual(groups, [('alice', 2), ('bob', 1)])
            item = conn.execute("SELECT id,favorited FROM information_items WHERE owner_id='alice'").fetchone()
            self.assertEqual(item[1], 1)
            refs = json.loads(conn.execute('SELECT item_refs FROM information_reviews').fetchone()[0])
            self.assertEqual(refs[0]['id'], item[0])
            self.assertEqual(refs[1]['id'], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM assets').fetchone()[0], 3)
            groups = conn.execute('SELECT id,owner_id,project_id,asset_id FROM information_groups').fetchall()
            for group_id, owner, project, asset_id in groups:
                asset = json.loads(conn.execute('SELECT data FROM assets WHERE id=?', (asset_id,)).fetchone()[0])
                self.assertEqual(asset['owner_id'], owner)
                self.assertEqual(asset['project_id'], project)
                self.assertEqual(asset['kind'], 'information')
                self.assertEqual(asset['content']['group_id'], group_id)
            self.assertFalse(conn.execute('PRAGMA foreign_key_check').fetchall())
        second = import_data(self.db, self.accounts, self.data, apply=True)
        self.assertTrue(all(row['inserted'] == 0 for row in second['tables'].values()))

    def test_invalid_cross_user_reference_rolls_back(self):
        self.data['information_items'][0]['group_id'] = 4
        before = self.db.read_bytes()
        with self.assertRaisesRegex(ValueError, '分组归属异常'):
            import_data(self.db, self.accounts, self.data, apply=True)
        self.assertEqual(before, self.db.read_bytes())

    def test_unknown_remote_accounts_are_not_imported(self):
        result = import_data(self.db, [], self.data)
        self.assertEqual(result['mapped_accounts'], 0)
        self.assertEqual(result['tables']['information_items']['unmatched'], 2)

    def test_dedicated_history_project_is_created_only_on_apply(self):
        options = {'alice@example.com': 'new:历史资讯'}
        result = import_data(self.db, self.accounts, self.data, projects=options)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM projects').fetchone()[0], 2)
        applied = import_data(self.db, self.accounts, self.data, projects=options, apply=True)
        self.assertEqual(result['projects'], applied['projects'])
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute('SELECT data FROM projects').fetchall()
            self.assertEqual(sum(json.loads(row[0])['name'] == '历史资讯' for row in rows), 1)

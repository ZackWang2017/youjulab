import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import tarfile
import zipfile
from unittest.mock import patch

import config
from desktop_data import backup_sqlite, archive_backup, extract_backup


class DesktopStorageTests(unittest.TestCase):
    def test_archives_round_trip_and_reject_unsafe_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            (source / 'manifest.json').write_text('{}')
            for extension in ('zip', 'tar.gz'):
                archive = root / ('backup.' + extension)
                archive_backup(source, archive)
                destination = root / ('restore-' + extension)
                extract_backup(archive, destination)
                self.assertEqual((destination / 'manifest.json').read_text(), '{}')
            bad = root / 'bad.zip'
            with zipfile.ZipFile(bad, 'w') as archive:
                archive.writestr('../escaped', 'bad')
            with self.assertRaises(ValueError):
                extract_backup(bad, root / 'bad-extract')
            self.assertFalse((root / 'escaped').exists())
            linked = root / 'linked.tar.gz'
            with tarfile.open(linked, 'w:gz') as archive:
                member = tarfile.TarInfo('link')
                member.type = tarfile.SYMTYPE
                member.linkname = '../escaped'
                archive.addfile(member)
            with self.assertRaises(ValueError):
                extract_backup(linked, root / 'linked-extract')

    def test_default_office_store_is_persistent(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(config.office_store_mode(), 'sqlite')

    def test_os_user_data_paths(self):
        with patch('config.sys.platform', 'darwin'), patch('config.Path.home', return_value=Path('/home/test')):
            self.assertEqual(config.user_data_root(), Path('/home/test/Library/Application Support/youjulab-desktop'))
        with patch('config.sys.platform', 'win32'), patch.dict(os.environ, {'APPDATA': '/roaming/test'}):
            self.assertEqual(config.user_data_root(), Path('/roaming/test/youjulab-desktop'))

    def test_desktop_always_keeps_vectors_local(self):
        with patch.dict(os.environ, {'OFFICE_DESKTOP': '1', 'OFFICE_VECTOR_BACKEND': 'pg'}):
            self.assertEqual(config.vector_backend(), 'sqlite')

    def test_changed_remote_setting_is_read_without_backend_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'app-config.json'
            with patch.dict(os.environ, {'OFFICE_DESKTOP_CONFIG': str(path)}):
                path.write_text(json.dumps({'remoteBaseUrl': 'http://192.168.0.5:8000'}))
                self.assertEqual(config.mcp_remote_base(), 'http://192.168.0.5:8000')
                path.write_text(json.dumps({'remoteBaseUrl': 'https://other.example.com'}))
                self.assertEqual(config.mcp_remote_base(), 'https://other.example.com')
                self.assertEqual(config.mcp_local_base(), 'https://other.example.com')

    def test_backup_captures_wal_without_external_sqlite_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'source.sqlite', Path(tmp) / 'backup.sqlite'
            with sqlite3.connect(source) as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('CREATE TABLE example (value TEXT)')
                conn.execute("INSERT INTO example VALUES ('preserved')")
                conn.commit()
                backup_sqlite(str(source), str(target))
            with sqlite3.connect(target) as restored:
                self.assertEqual(restored.execute('SELECT value FROM example').fetchone()[0], 'preserved')
            with self.assertRaises(ValueError):
                backup_sqlite(str(source), str(source))

    def test_account_cache_is_isolated_by_remote_server(self):
        from server import fetch_remote_account, _remote_me_cache
        _remote_me_cache.clear()
        try:
            with patch('server.mcp_remote_base', side_effect=['http://first', 'http://second']), patch(
                'server._http_get_json', side_effect=[(200, {'id': 'a', 'email': 'a@example.com'}),
                                                     (200, {'id': 'b', 'email': 'b@example.com'})]) as get:
                self.assertEqual(fetch_remote_account('same-token')['id'], 'a')
                self.assertEqual(fetch_remote_account('same-token')['id'], 'b')
                self.assertEqual(get.call_count, 2)
        finally:
            _remote_me_cache.clear()

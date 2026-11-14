import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from db.migrate_sqlite_to_postgres import inspect_source, migrate, copy_data


class PostgresMigrationTests(unittest.TestCase):
    def test_dry_run_keeps_source_untouched_and_does_not_connect_postgres(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'finance.sqlite'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE accounts (id TEXT PRIMARY KEY, password_hash TEXT)')
                conn.execute("INSERT INTO accounts VALUES ('original-id','original-hash')")
            before = path.read_bytes()
            with patch('db.migrate_sqlite_to_postgres.psycopg.connect') as pg:
                report = migrate(path, Path(tmp) / 'backups')
                pg.assert_not_called()
            self.assertEqual(report['rows'], {'accounts': 1})
            self.assertEqual(before, path.read_bytes())
            self.assertFalse((Path(tmp) / 'backups').exists())

    def test_unknown_tables_cannot_be_silently_lost(self):
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE TABLE custom_records (id INT)')
            with self.assertRaisesRegex(ValueError, '未纳入迁移'):
                inspect_source(conn)

    def test_news_history_is_explicitly_excluded_from_remote_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'finance.sqlite'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE information_groups (id INTEGER)')
                conn.execute('INSERT INTO information_groups VALUES (1)')
            with patch('db.migrate_sqlite_to_postgres.psycopg.connect') as pg:
                report = migrate(path, Path(tmp) / 'backups')
            pg.assert_not_called()
            self.assertEqual(report['rows'], {})
            self.assertEqual(report['skipped_local_information'], {'information_groups': 1})

    def test_invalid_foreign_keys_abort(self):
        with sqlite3.connect(':memory:') as conn:
            conn.executescript('CREATE TABLE portfolios (id INT PRIMARY KEY);'
                'CREATE TABLE portfolio_holdings (id INT, portfolio_id INT REFERENCES portfolios(id));'
                'INSERT INTO portfolio_holdings VALUES (1,99);')
            with self.assertRaisesRegex(ValueError, '外键异常'):
                inspect_source(conn)

    def test_existing_target_data_is_never_overwritten(self):
        target = Mock()
        target.execute.return_value.fetchone.return_value = {'n': 1}
        with sqlite3.connect(':memory:') as source:
            with self.assertRaisesRegex(ValueError, '非空'):
                copy_data(source, target, {})
        self.assertFalse(target.cursor.called)

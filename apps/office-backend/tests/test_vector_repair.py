from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

import sqlite_vec
from repair_pdf_vectors import connect, repair, verify


def snapshot(path, content_hash='original'):
    with sqlite3.connect(path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.executescript('''
            CREATE TABLE pdf_index (document_id TEXT PRIMARY KEY, content_hash TEXT, chunk_count INT, status TEXT);
            CREATE TABLE pdf_chunks (document_id TEXT, chunk_id TEXT, text TEXT);
            CREATE TABLE pdf_layout_blocks (document_id TEXT);
            CREATE VIRTUAL TABLE pdf_chunk_vec USING vec0(document_id TEXT, chunk_id TEXT, embedding float[2]);
        ''')
        conn.execute("INSERT INTO pdf_index VALUES ('doc',?,1,'ready')", (content_hash,))
        conn.execute("INSERT INTO pdf_chunks VALUES ('doc','chunk','example')")
        conn.execute("INSERT INTO pdf_chunk_vec (document_id,chunk_id,embedding) VALUES ('doc','chunk',?)", (struct.pack('2f', 1, 0),))


class VectorRepairTests(unittest.TestCase):
    def test_restore_and_preserve_original_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'good.sqlite', Path(tmp) / 'damaged.sqlite'
            snapshot(source)
            with sqlite3.connect(target) as conn:
                conn.execute('CREATE TABLE unrelated (value TEXT)')
            original = target.read_bytes()
            result = repair(target, source)
            self.assertEqual((Path(result['backup_directory']) / target.name).read_bytes(), original)
            with connect(target) as conn:
                self.assertEqual(verify(conn)['pdf_chunk_vec'], 1)

    def test_changed_document_does_not_get_stale_vectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'good.sqlite', Path(tmp) / 'damaged.sqlite'
            snapshot(source)
            snapshot(target, 'changed')
            result = repair(target, source)
            self.assertEqual(result['reindex_document_ids'], ['doc'])
            self.assertEqual(result['counts']['pdf_chunk_vec'], 0)

    def test_bad_snapshot_never_replaces_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'bad.sqlite', Path(tmp) / 'target.sqlite'
            snapshot(target)
            source.write_bytes(b'not a database')
            original = target.read_bytes()
            with self.assertRaises(sqlite3.DatabaseError):
                repair(target, source)
            self.assertEqual(target.read_bytes(), original)

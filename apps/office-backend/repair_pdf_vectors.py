"""Restore an offline sqlite-vec cache from a verified snapshot, keeping damaged files."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3

import sqlite_vec


@contextmanager
def connect(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    try:
        yield conn
    finally:
        conn.close()


def verify(conn):
    checks = [row[0] for row in conn.execute('PRAGMA integrity_check')]
    if checks != ['ok']:
        raise ValueError('恢复来源未通过完整性检查。')
    if conn.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('恢复来源存在外键异常。')
    counts = {table: conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
              for table in ('pdf_index', 'pdf_chunks', 'pdf_chunk_vec', 'pdf_layout_blocks')}
    if counts['pdf_chunks'] != counts['pdf_chunk_vec']:
        raise ValueError('文本块与向量数量不一致。')
    for row in conn.execute('''SELECT i.document_id, i.chunk_count, count(c.chunk_id) AS actual
            FROM pdf_index i LEFT JOIN pdf_chunks c ON c.document_id=i.document_id
            WHERE i.status='ready' GROUP BY i.document_id HAVING i.chunk_count <> count(c.chunk_id)'''):
        raise ValueError(f"文档 {row['document_id']} 的索引计数不一致。")
    missing = conn.execute('''SELECT count(*) FROM pdf_chunks c LEFT JOIN pdf_chunk_vec v
        ON v.document_id=c.document_id AND v.chunk_id=c.chunk_id WHERE v.chunk_id IS NULL''').fetchone()[0]
    if missing:
        raise ValueError('存在缺失的向量。')
    first = conn.execute('SELECT document_id, embedding FROM pdf_chunk_vec LIMIT 1').fetchone()
    if first:
        hit = conn.execute('''SELECT chunk_id FROM pdf_chunk_vec
            WHERE embedding MATCH ? AND k=1 AND document_id=?''',
            (first['embedding'], first['document_id'])).fetchone()
        if hit is None:
            raise ValueError('向量检索验证失败。')
    return counts


def fingerprint(path):
    return [(str(p), p.stat().st_size, p.stat().st_mtime_ns)
            for p in (path, Path(str(path) + '-wal'), Path(str(path) + '-shm')) if p.exists()]


def repair(database, snapshot):
    database, snapshot = Path(database).resolve(), Path(snapshot).resolve()
    if database == snapshot:
        raise ValueError('恢复来源不能是待修复文件本身。')
    before = fingerprint(database)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup_dir = database.parent / f'vector-repair-{stamp}'
    backup_dir.mkdir(mode=0o700)
    for filename, _, _ in before:
        shutil.copy2(filename, backup_dir / Path(filename).name)
    stage = backup_dir / 'restored.sqlite'
    report = {'source': str(snapshot), 'backup_directory': str(backup_dir), 'reindex_document_ids': []}
    try:
        with connect(snapshot) as src:
            verify(src)
            with sqlite3.connect(stage) as dest:
                src.backup(dest)
        with connect(backup_dir / database.name) as damaged:
            recovered = []
            try:
                for row in damaged.execute('SELECT * FROM pdf_index NOT INDEXED'):
                    recovered.append(dict(row))
            except sqlite3.DatabaseError:
                pass
        with sqlite3.connect(stage) as dest:
            dest.enable_load_extension(True)
            sqlite_vec.load(dest)
            dest.enable_load_extension(False)
            dest.execute('PRAGMA journal_mode=DELETE')
            for row in recovered:
                cached = dest.execute('SELECT content_hash FROM pdf_index WHERE document_id=?', (row['document_id'],)).fetchone()
                if not cached or cached[0] != row['content_hash']:
                    document = row['document_id']
                    report['reindex_document_ids'].append(document)
                    for table in ('pdf_chunk_vec', 'pdf_chunks', 'pdf_layout_blocks', 'pdf_index'):
                        dest.execute(f'DELETE FROM {table} WHERE document_id=?', (document,))
            dest.commit()
        with connect(stage) as restored:
            report['counts'] = verify(restored)
        if fingerprint(database) != before:
            raise RuntimeError('源文件已变化，请退出后台服务后重新修复。')
        stage.chmod(0o600)
        os.replace(stage, database)
        for suffix in ('-wal', '-shm'):
            Path(str(database) + suffix).unlink(missing_ok=True)
        with connect(database) as restored:
            report['counts'] = verify(restored)
        (backup_dir / 'repair.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        return report
    finally:
        stage.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--snapshot', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(repair(args.database, args.snapshot), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

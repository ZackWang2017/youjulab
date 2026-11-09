"""Offline per-account export/restore. Never package a shared SQLite database."""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

import sqlite_vec

OWNED = {'projects': 'owner_id', 'assets': 'owner_id', 'digest_jobs': 'user_id',
    'document_skills': 'user_id', 'assistant_sessions': 'user_id', 'sheet_cache': 'user_id',
    'sheet_charts': 'user_id', 'user_model_configs': 'user_id', 'pdf_folders': 'user_id',
    'document_folders': 'user_id'}
VECTOR_TABLES = ('pdf_index', 'pdf_chunks', 'pdf_layout_blocks', 'pdf_chunk_vec')


def db(path, readonly=False, vectors=False):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) if readonly else sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if vectors:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    return conn


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def user(conn, email):
    matches = [json.loads(r['data']) for r in conn.execute('SELECT data FROM users')
               if json.loads(r['data']).get('email', '').lower() == email.lower()]
    if len(matches) != 1:
        raise ValueError('当前邮箱未绑定唯一的本地账号，请先登录。')
    return matches[0]


def snapshot(source, target, vectors=False):
    with closing(db(source, True, vectors)) as src, closing(db(target)) as dst:
        src.backup(dst)


def export_user(office, vectors, files, email, account_id, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {'version': 1, 'email': email.lower(), 'account_id': account_id, 'tables': {}, 'vectors': None}
    with closing(db(office, True)) as src, closing(sqlite3.connect(':memory:')) as conn:
        conn.row_factory = sqlite3.Row
        src.backup(conn)
        local = user(conn, email)
        uid = local['id']
        payload['local_user_id'] = uid
        payload['preferences'] = {k: v for k, v in local.items() if k not in ('password_hash', 'access_token', 'id', 'email')}
        known = tables(conn)
        unknown = known - set(OWNED) - {'users', 'quant_runs', 'sqlite_sequence', 'local_backup_records'}
        if unknown:
            raise ValueError(f'未识别的本地数据表，停止备份以避免遗漏：{sorted(unknown)}')
        for table, key in OWNED.items():
            payload['tables'][table] = [{**json.loads(r['data']), '__backup_key': r['id']} for r in conn.execute(f'SELECT id,data FROM {table}')
                if json.loads(r['data']).get(key) == uid] if table in known else []
        payload['quant_runs'] = [dict(r) for r in conn.execute('SELECT * FROM quant_runs WHERE owner=?', (uid,))] if 'quant_runs' in known else []
    pdfs = {a['id']: a for a in payload['tables']['assets'] if a.get('kind') == 'pdf'}
    project_ids = {p['id'] for p in payload['tables']['projects']}
    for asset in pdfs.values():
        project = asset.get('project_id') or 'user'
        if project != 'user' and project not in project_ids:
            raise ValueError('PDF 项目归属异常。')
        relative = Path(uid) / project / f'{asset["id"]}.pdf'
        if len(relative.parts) != 3 or any(part in ('.', '..') for part in relative.parts):
            raise ValueError('PDF 标识或归属路径无效。')
        root = Path(files).resolve()
        canonical = root / relative
        raw = Path(asset.get('file_path') or '')
        # Prefer migrated userData over stale but still-existing development files.
        source = canonical if canonical.is_file() else raw if raw.is_file() else None
        if source is None:
            if asset.get('file_path'):
                raise ValueError(f'PDF 文件缺失，停止备份：{asset.get("name", asset["id"])}')
            continue
        resolved = source.resolve()
        # Legacy roots may differ, but the complete owner/project/asset suffix must match.
        if (source.is_symlink() or any(parent.is_symlink() for parent in (source.parent, source.parent.parent))
            or tuple(resolved.parts[-3:]) != relative.parts):
            raise ValueError(f'PDF 文件归属路径异常，停止备份：{asset.get("name", asset["id"])}')
        target = destination / 'office-files' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        asset['file_path'] = 'office-files/' + relative.as_posix()
    if Path(vectors).is_file():
        with closing(db(vectors, True, True)) as conn:
            conn.execute('BEGIN')
            ids = {r[0] for r in conn.execute('SELECT document_id FROM pdf_index WHERE user_id=?', (uid,)) if r[0] in pdfs}
            payload['vectors'] = {'rows': {}}
            for table in VECTOR_TABLES:
                rows = []
                for document in ids:
                    for row in conn.execute(f'SELECT * FROM {table} WHERE document_id=?', (document,)):
                        item = dict(row)
                        if table == 'pdf_chunk_vec':
                            item['embedding'] = base64.b64encode(item['embedding']).decode('ascii')
                        rows.append(item)
                payload['vectors']['rows'][table] = rows
    output = destination / 'user-data.json'
    output.write_text(json.dumps(payload, ensure_ascii=False))
    output.chmod(0o600)
    return {'localUserId': uid, 'assets': len(payload['tables']['assets']), 'pdfs': len(pdfs)}


def validate(payload, uid, email, account_id):
    if payload.get('version') != 1 or payload.get('local_user_id') != uid or payload.get('email') != email.lower() or payload.get('account_id') != account_id:
        raise ValueError('备份与当前用户身份不一致。')
    if set(payload.get('tables', {})) != set(OWNED):
        raise ValueError('备份表结构不完整。')
    for table, key in OWNED.items():
        for row in payload['tables'][table]:
            if row.get(key) != uid or not isinstance(row.get('__backup_key'), str):
                raise ValueError('备份包含其他账号的数据，已拒绝恢复。')
    if any(row.get('owner') != uid for row in payload.get('quant_runs', [])):
        raise ValueError('研究记录归属异常。')
    pdfs = {r['id'] for r in payload['tables']['assets'] if r.get('kind') == 'pdf'}
    for table, rows in (payload.get('vectors') or {}).get('rows', {}).items():
        if table not in VECTOR_TABLES or any(r.get('document_id') not in pdfs for r in rows):
            raise ValueError('向量索引包含其他账号的资产。')
        if table == 'pdf_index' and any(r.get('user_id') != uid for r in rows):
            raise ValueError('向量索引归属异常。')


def restore_user(office, vectors, files, email, account_id, source):
    source = Path(source)
    payload = json.loads((source / 'user-data.json').read_text())
    with closing(db(office, True)) as conn:
        uid = user(conn, email)['id']
    validate(payload, uid, email, account_id)
    # Prepare complete replacement databases offline; other users are copied unchanged.
    Path(files).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='user-restore-', dir=Path(office).parent) as work, tempfile.TemporaryDirectory(prefix='.user-restore-', dir=files) as file_work:
        work = Path(work)
        office_copy, vector_copy = work / 'office.sqlite', work / 'vectors.sqlite'
        snapshot(office, office_copy)
        if Path(vectors).exists():
            snapshot(vectors, vector_copy, True)
        with closing(db(office_copy)) as conn:
            current_assets = [json.loads(r['data']) for r in conn.execute('SELECT data FROM assets')]
            old_pdf_ids = {a['id'] for a in current_assets if a.get('owner_id') == uid and a.get('kind') == 'pdf'}
            for table, key in OWNED.items():
                if table not in tables(conn):
                    conn.execute(f'CREATE TABLE {table} (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
                for row in payload['tables'][table]:
                    existing = conn.execute(f'SELECT data FROM {table} WHERE id=?', (row['__backup_key'],)).fetchone()
                    if existing and json.loads(existing[0]).get(key) != uid:
                        raise ValueError('备份标识与其他用户的数据冲突，未执行恢复。')
                conn.execute(f'DELETE FROM {table} WHERE json_extract(data,?)=?', ('$.' + key, uid))
                conn.executemany(f'INSERT INTO {table} (id,data) VALUES (?,?)',
                    [(r['__backup_key'], json.dumps({k:v for k,v in r.items() if k != '__backup_key'}, ensure_ascii=False)) for r in payload['tables'][table]])
            local = user(conn, email)
            local.update({k: v for k, v in payload.get('preferences', {}).items() if k not in ('id','email','password_hash','access_token')})
            conn.execute('UPDATE users SET data=? WHERE id=?', (json.dumps(local, ensure_ascii=False), uid))
            if 'quant_runs' in tables(conn):
                for row in payload.get('quant_runs', []):
                    if set(row) != {r[1] for r in conn.execute('PRAGMA table_info(quant_runs)')}:
                        raise ValueError('研究记录字段不兼容。')
                    other = conn.execute('SELECT owner FROM quant_runs WHERE id=?', (row['id'],)).fetchone()
                    if other and other[0] != uid:
                        raise ValueError('研究记录标识冲突。')
                conn.execute('DELETE FROM quant_runs WHERE owner=?', (uid,))
                for row in payload.get('quant_runs', []):
                    columns = list(row)
                    conn.execute(f'INSERT INTO quant_runs ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', tuple(row.values()))
            conn.commit()
        with closing(db(vector_copy, vectors=True)) as conn:
            vector_data = payload.get('vectors')
            for table in VECTOR_TABLES:
                if table not in tables(conn) and vector_data:
                    # Schema is taken from the installed implementation, not executable archive SQL.
                    if table == 'pdf_chunk_vec':
                        from config import embedding_dim
                        conn.execute(f'CREATE VIRTUAL TABLE pdf_chunk_vec USING vec0(document_id TEXT, chunk_id TEXT, embedding float[{embedding_dim()}])')
                    else:
                        columns = {'pdf_index': 'document_id TEXT PRIMARY KEY,user_id TEXT,file_mtime INTEGER,content_hash TEXT,status TEXT,page_count INTEGER,chunk_count INTEGER,error TEXT,empty INTEGER,layout_version INTEGER,phase TEXT,progress_done INTEGER,progress_total INTEGER,updated_at TEXT',
                            'pdf_chunks': 'document_id TEXT,chunk_id TEXT,page INTEGER,ord INTEGER,text TEXT,PRIMARY KEY(document_id,chunk_id)',
                            'pdf_layout_blocks': 'document_id TEXT,block_id TEXT,page INTEGER,ord INTEGER,text TEXT,font_size REAL,bold INTEGER,x0 REAL,y0 REAL,x1 REAL,y1 REAL,indent REAL,y_ratio REAL,PRIMARY KEY(document_id,block_id)'}[table]
                        conn.execute(f'CREATE TABLE {table} ({columns})')
            new_pdf_ids = {a['id'] for a in payload['tables']['assets'] if a.get('kind') == 'pdf'}
            affected = old_pdf_ids | new_pdf_ids
            if 'pdf_index' in tables(conn):
                for document in affected:
                    other = conn.execute('SELECT user_id FROM pdf_index WHERE document_id=?', (document,)).fetchone()
                    if other and other[0] != uid:
                        raise ValueError('向量资产标识与其他用户冲突。')
                for table in reversed(VECTOR_TABLES):
                    if table in tables(conn):
                        conn.executemany(f'DELETE FROM {table} WHERE document_id=?', [(x,) for x in affected])
            if vector_data:
                for table in VECTOR_TABLES:
                    allowed = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
                    for raw in vector_data['rows'].get(table, []):
                        row = dict(raw)
                        if set(row) - allowed:
                            raise ValueError('索引字段不兼容。')
                        if table == 'pdf_chunk_vec':
                            row['embedding'] = base64.b64decode(row['embedding'], validate=True)
                        cols = list(row)
                        conn.execute(f'INSERT INTO {table} ({",".join(cols)}) VALUES ({",".join("?" for _ in cols)})', tuple(row.values()))
            conn.commit()
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('恢复后的索引完整性校验失败。')
        target_files = Path(files) / uid
        expected = {Path(a['file_path']).relative_to('office-files').as_posix() for a in payload['tables']['assets']
                    if a.get('kind') == 'pdf' and a.get('file_path')}
        for name in expected:
            candidate = source / 'office-files' / name
            if not candidate.resolve().is_relative_to((source / 'office-files' / uid).resolve()) or candidate.is_symlink() or not candidate.is_file():
                raise ValueError('PDF 附件缺失或路径不安全。')
        staged_files = Path(file_work) / 'incoming-files'
        staged_files.mkdir(mode=0o700)
        for name in expected:
            relative = Path(name).relative_to(uid)
            dest = staged_files / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / 'office-files' / name, dest)
        originals = []
        office_copy.chmod(0o600)
        vector_copy.chmod(0o600)
        try:
            for original, replacement in ((Path(office), office_copy), (Path(vectors), vector_copy), (target_files, staged_files)):
                original.parent.mkdir(parents=True, exist_ok=True)
                saved = (Path(file_work) if original == target_files else work) / ('original-' + original.name)
                if original.exists():
                    os.replace(original, saved)
                originals.append((original, saved))
                os.replace(replacement, original)
            for database in (office, vectors):
                for suffix in ('-wal', '-shm'):
                    Path(str(database) + suffix).unlink(missing_ok=True)
        except Exception:
            for original, saved in reversed(originals):
                if original.is_dir():
                    shutil.rmtree(original)
                else:
                    original.unlink(missing_ok=True)
                if saved.exists():
                    os.replace(saved, original)
            raise
    return {'ok': True, 'localUserId': uid}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('export', 'restore'))
    for name in ('office', 'vectors', 'files', 'email', 'account-id', 'directory'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    fn = export_user if args.operation == 'export' else restore_user
    print(json.dumps(fn(args.office, args.vectors, args.files, args.email, args.account_id, args.directory)))


if __name__ == '__main__':
    main()

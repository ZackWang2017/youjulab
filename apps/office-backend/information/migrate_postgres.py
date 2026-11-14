"""Explicit, non-destructive news import from PostgreSQL into local Office SQLite."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4, uuid5, NAMESPACE_URL

from .repository.information_repository import INFORMATION_TABLES, initialize_schema


def value(raw):
    if isinstance(raw, (datetime, date)):
        return raw.isoformat()
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (dict, list)):
        return json.dumps(raw, ensure_ascii=False)
    return raw


def account_mapping(conn, accounts):
    users = [json.loads(row[0]) for row in conn.execute('SELECT data FROM users')]
    mapping = {}
    for account in accounts:
        email = str(account['email']).strip().lower()
        matches = [user for user in users if str(user.get('email', '')).strip().lower() == email]
        if len(matches) > 1:
            raise ValueError('本地存在重复邮箱，无法安全匹配资讯账号。')
        if matches:
            mapping[str(account['id'])] = matches[0]['id']
    return mapping


def project_mapping(conn, accounts, mapping, data, overrides):
    projects = [json.loads(row[0]) for row in conn.execute('SELECT data FROM projects')]
    result = {}
    emails = {str(account['id']): str(account['email']).strip().lower() for account in accounts}
    for source_owner in {str(row['owner_id']) for row in data['information_groups']}:
        owner = mapping.get(source_owner)
        if not owner:
            continue
        available = [project for project in projects if project.get('owner_id') == owner]
        chosen = overrides.get(emails[source_owner])
        if chosen and chosen.startswith('new:'):
            name = chosen[4:].strip()
            if not name:
                raise ValueError('新项目名称不能为空。')
            matches = [project for project in available if project.get('name') == name]
            if len(matches) > 1:
                raise ValueError('存在多个同名项目，请直接指定项目 ID。')
            if matches:
                chosen = matches[0]['id']
            else:
                chosen = uuid5(NAMESPACE_URL, f'youjulab:news-import:{owner}:{name}').hex
                if any(project['id'] == chosen for project in projects):
                    raise ValueError('迁移项目标识冲突。')
                stamp = datetime.now(timezone.utc).isoformat()
                project = {'id': chosen, 'name': name, 'owner_id': owner, 'created_at': stamp, 'updated_at': stamp}
                conn.execute('INSERT INTO projects (id,data) VALUES (?,?)', (chosen, json.dumps(project, ensure_ascii=False)))
                projects.append(project)
        elif chosen:
            if not any(project['id'] == chosen for project in available):
                raise ValueError('指定的迁入项目不存在或不属于对应用户。')
        elif len(available) == 1:
            chosen = available[0]['id']
        else:
            raise ValueError(f'账号 {emails[source_owner]} 有多个项目，请使用 --project 邮箱=项目ID 指定归属。')
        result[source_owner] = chosen
    return result


def ensure_asset(conn, owner, project, group_id, name, asset_id=None):
    asset_id = asset_id or uuid4().hex
    existing = conn.execute('SELECT data FROM assets WHERE id=?', (asset_id,)).fetchone()
    if existing:
        asset = json.loads(existing[0])
        if (asset.get('owner_id') != owner or asset.get('project_id') != project
                or asset.get('kind') != 'information'):
            raise ValueError('资讯资产标识与已有资产归属冲突。')
    else:
        asset = {'id': asset_id, 'owner_id': owner, 'project_id': project, 'kind': 'information',
                 'name': name, 'visibility': 'project', 'revision': 0,
                 'content': {'format': 'invp-information-group-v1', 'group_id': group_id},
                 'file_path': None, 'filename': None, 'size': None,
                 'updated_at': datetime.now(timezone.utc).isoformat()}
        conn.execute('INSERT INTO assets (id,data) VALUES (?,?)', (asset_id, json.dumps(asset, ensure_ascii=False)))
    conn.execute('UPDATE information_groups SET asset_id=?,project_id=?,name=? WHERE id=? AND owner_id=?',
                 (asset_id, project, name, group_id, owner))


def merge_rows(conn, data, mapping, projects):
    initialize_schema(conn)
    report = {table: {'inserted': 0, 'existing': 0, 'unmatched': 0} for table in INFORMATION_TABLES}
    ids = {table: {} for table in INFORMATION_TABLES}
    for table in INFORMATION_TABLES:
        allowed = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
        for original in data[table]:
            source_owner = str(original['owner_id'])
            owner = mapping.get(source_owner)
            if owner is None:
                report[table]['unmatched'] += 1
                continue
            source_id = int(original['id'])
            row = {key: value(raw) for key, raw in original.items()}
            if set(row) - allowed:
                raise ValueError(f'源资讯字段不兼容：{table}')
            row['owner_id'] = owner
            if table == 'information_groups':
                project = projects[source_owner]
                row['project_id'] = project
                if row['kind'] == 'favorites':
                    row['name'] = '资讯收藏栏'
                    existing = conn.execute("SELECT id,asset_id FROM information_groups WHERE owner_id=? AND kind='favorites' AND (project_id=? OR project_id IS NULL OR project_id='') ORDER BY project_id DESC", (owner, project)).fetchall()
                else:
                    existing = conn.execute("SELECT id,asset_id FROM information_groups WHERE owner_id=? AND name=? AND kind=? AND (project_id=? OR project_id IS NULL OR project_id='')",
                                            (owner, row['name'], row['kind'], project)).fetchall()
                if len(existing) > 1:
                    raise ValueError('本地同名资讯组不唯一，已停止迁移。')
            else:
                group = ids['information_groups'].get((source_owner, int(row['group_id'])))
                if group is None:
                    raise ValueError('源资讯分组归属异常，已停止迁移。')
                row['group_id'] = group
                if table == 'information_items':
                    existing = conn.execute('SELECT id FROM information_items WHERE owner_id=? AND group_id=? AND title=?', (owner, group, row['title'])).fetchall()
                else:
                    existing = conn.execute('SELECT id FROM information_reviews WHERE owner_id=? AND group_id=? AND review_date=?',
                                            (owner, group, row['review_date'])).fetchall()
                    refs = json.loads(row.get('item_refs') or '[]')
                    if not isinstance(refs, list):
                        raise ValueError('源点评引用格式无效。')
                    for ref in refs:
                        if not isinstance(ref, dict):
                            raise ValueError('源点评引用格式无效。')
                        # Deleted source articles retain their title/URL, never another user's ID.
                        ref['id'] = ids['information_items'].get((source_owner, int(ref.get('id') or 0)), 0)
                    row['item_refs'] = json.dumps(refs, ensure_ascii=False)
            if existing:
                new_id = existing[0][0]
                report[table]['existing'] += 1
            else:
                row.pop('id')
                columns = list(row)
                cursor = conn.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', tuple(row.values()))
                new_id = cursor.lastrowid
                report[table]['inserted'] += 1
            if table == 'information_groups':
                ensure_asset(conn, owner, projects[source_owner], new_id, row['name'], existing[0][1] if existing else None)
            ids[table][(source_owner, source_id)] = new_id
    if conn.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('导入后的资讯关联校验失败。')
    return report, ids


def import_data(database, accounts, data, *, apply=False, projects=None):
    database = Path(database).expanduser().resolve()
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
        mapping = account_mapping(source, accounts)
        with closing(sqlite3.connect(':memory:')) as preview:
            source.backup(preview)
            destinations = project_mapping(preview, accounts, mapping, data, projects or {})
            report, _ = merge_rows(preview, data, mapping, destinations)
    result = {'mode': 'dry-run', 'database': str(database), 'tables': report, 'mapped_accounts': len(mapping), 'projects': destinations}
    if not apply:
        return result
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = database.parent/'migration-backups'/('information-'+stamp)
    directory.mkdir(parents=True, mode=0o700)
    snapshot = directory/'office.sqlite'
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(snapshot)) as backup:
        source.backup(backup)
    snapshot.chmod(0o600)
    with closing(sqlite3.connect(database, timeout=30)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        try:
            actual_mapping = account_mapping(conn, accounts)
            actual_destinations = project_mapping(conn, accounts, actual_mapping, data, projects or {})
            actual, ids = merge_rows(conn, data, actual_mapping, actual_destinations)
            if actual_mapping != mapping or actual != report or actual_destinations != destinations:
                raise ValueError('预检后本地数据发生变化，已停止导入，请退出 App 后重试。')
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('导入后的数据库完整性检查失败。')
            # Verify each mapped record exists and remains bound to the expected local owner.
            for table in INFORMATION_TABLES:
                for (owner, _), new_id in ids[table].items():
                    stored = conn.execute(f'SELECT owner_id FROM {table} WHERE id=?', (new_id,)).fetchone()
                    if not stored or stored[0] != mapping[owner]:
                        raise ValueError('导入后的用户归属校验失败。')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    result.update(mode='applied', backup=str(snapshot))
    (directory/'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.conninfo import make_conninfo
    from config import sqlite_path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='退出桌面端后显式导入；默认只预检')
    parser.add_argument('--office', type=Path, default=sqlite_path())
    parser.add_argument('--project', action='append', default=[], metavar='EMAIL=PROJECT_ID')
    parser.add_argument('--create-project', action='append', default=[], metavar='EMAIL=PROJECT_NAME')
    args = parser.parse_args()
    dsn = os.environ.get('FINANCE_DATABASE_URL') or make_conninfo(
        host=os.environ.get('FINANCE_DB_HOST', 'localhost'), port=os.environ.get('FINANCE_DB_PORT', '5432'),
        user=os.environ.get('FINANCE_DB_USER', 'zackwang'), password=os.environ.get('FINANCE_DB_PASSWORD', ''),
        dbname=os.environ.get('FINANCE_DB_NAME', 'finresearch'))
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as source:
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        accounts = source.execute('SELECT id,email FROM accounts').fetchall()
        data = {table: source.execute(f'SELECT * FROM {table} ORDER BY id').fetchall() for table in INFORMATION_TABLES}
    destinations = {}
    for choice in args.project:
        email, separator, project = choice.partition('=')
        if not separator or not email.strip() or not project.strip():
            parser.error('--project 格式必须为 邮箱=项目ID')
        destinations[email.strip().lower()] = project.strip()
    for choice in args.create_project:
        email, separator, name = choice.partition('=')
        if not separator or not email.strip() or not name.strip():
            parser.error('--create-project 格式必须为 邮箱=项目名称')
        if email.strip().lower() in destinations:
            parser.error('同一账号不可重复指定迁入项目')
        destinations[email.strip().lower()] = 'new:' + name.strip()
    result = import_data(args.office, accounts, data, apply=args.apply, projects=destinations)
    result['source_rows'] = {table: len(rows) for table, rows in data.items()}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

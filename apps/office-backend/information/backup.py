"""User-scoped news rows in the existing local backup archive."""
TABLES = ('information_groups', 'information_items', 'information_reviews')


def export_rows(conn, owner_id):
    existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {table: [dict(row) for row in conn.execute(f'SELECT * FROM {table} WHERE owner_id=?', (owner_id,))]
            if table in existing else [] for table in TABLES}


def validate_rows(data, owner_id, *, projects=None, assets=None):
    if not isinstance(data, dict) or set(data) != set(TABLES):
        raise ValueError('资讯备份表结构不完整。')
    for table in TABLES:
        rows = data[table]
        if not isinstance(rows, list):
            raise ValueError('资讯备份格式无效。')
        seen = set()
        for row in rows:
            if (not isinstance(row, dict) or row.get('owner_id') != owner_id
                    or type(row.get('id')) is not int or row['id'] <= 0 or row['id'] in seen):
                raise ValueError('资讯备份包含其他账号或无效标识。')
            seen.add(row['id'])
    groups = {row['id'] for row in data['information_groups']}
    if projects is not None and assets is not None:
        project_ids = {row['id'] for row in projects if row.get('owner_id') == owner_id}
        asset_by_id = {row['id']: row for row in assets if row.get('owner_id') == owner_id}
        for row in data['information_groups']:
            project, asset_id = row.get('project_id'), row.get('asset_id')
            if project is None and asset_id is None:
                continue
            asset = asset_by_id.get(asset_id)
            if (project not in project_ids or not asset or asset.get('kind') != 'information'
                    or asset.get('project_id') != project or (asset.get('content') or {}).get('group_id') != row['id']):
                raise ValueError('资讯备份的项目或资产关联异常。')
    for table in TABLES[1:]:
        if any(row.get('group_id') not in groups for row in data[table]):
            raise ValueError('资讯条目或点评的分组归属异常。')


def restore_rows(conn, data, owner_id):
    from .repository.information_repository import initialize_schema

    validate_rows(data, owner_id)
    initialize_schema(conn)
    for table in TABLES:
        allowed = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
        for row in data[table]:
            if set(row) - allowed or allowed - set(row) - {'project_id', 'asset_id'}:
                raise ValueError('资讯备份字段不兼容。')
            existing = conn.execute(f'SELECT owner_id FROM {table} WHERE id=?', (row['id'],)).fetchone()
            if existing and existing[0] != owner_id:
                raise ValueError('资讯备份标识与其他用户的数据冲突。')
    for table in reversed(TABLES):
        conn.execute(f'DELETE FROM {table} WHERE owner_id=?', (owner_id,))
    for table in TABLES:
        for row in data[table]:
            columns = list(row)
            conn.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', tuple(row.values()))
    if conn.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('资讯备份关联校验失败。')

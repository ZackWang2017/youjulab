"""Copy a consistent SQLite snapshot into an EMPTY PostgreSQL database.

Dry-run is the default. No source writes, upserts, account-ID remapping or deletion.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
for layer in ('account-service/repository', 'portfolio-service/repository'):
    sys.path.insert(0, str(ROOT / layer))

from db.config import load_config
from db.schema import ALL_DDL
from account_repository import _DDL as ACCOUNT_DDL
from portfolio_repository import _DDL as PORTFOLIO_DDL
from scheduler.pg_run_store import _DDL as SCHEDULER_DDL

TABLES = ('accounts', 'securities', 'financial_item_mappings', 'financial_statements',
          'portfolios', 'portfolio_holdings', 'portfolio_transactions',
          'security_close_prices', 'fx_rates_daily', 'portfolio_nav_daily',
          'scheduled_task_runs', 'external_search_daily')
LOCAL_ONLY_TABLES = {'information_groups', 'information_items', 'information_reviews'}
USAGE_DDL = '''CREATE TABLE IF NOT EXISTS external_search_daily (
    engine TEXT NOT NULL, day DATE NOT NULL, count INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY (engine, day))'''


def read_source(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def inspect_source(conn):
    check = [r[0] for r in conn.execute('PRAGMA quick_check')]
    if check != ['ok']:
        raise ValueError('源 SQLite 完整性检查失败，停止迁移。')
    if conn.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('源 SQLite 存在外键异常，停止迁移。')
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    unknown = names - set(TABLES) - LOCAL_ONLY_TABLES
    if unknown:
        raise ValueError(f'存在未纳入迁移的表：{sorted(unknown)}')
    return {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in TABLES if t in names}


def copy_data(source, target, counts):
    for ddl in (*ALL_DDL, ACCOUNT_DDL, PORTFOLIO_DDL, SCHEDULER_DDL, USAGE_DDL):
        target.execute(ddl)
    # Guard all tables before importing any row. Never overwrite historical data.
    for table in TABLES:
        if target.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()['n']:
            raise ValueError(f'目标表 {table} 非空，请使用独立空库；不会覆盖已有数据。')
    for table, count in counts.items():
        columns = [r[1] for r in source.execute(f'PRAGMA table_info("{table}")')]
        meta = target.execute('''SELECT column_name, data_type FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s''', (table,)).fetchall()
        types = {r['column_name']: r['data_type'] for r in meta}
        if set(columns) - set(types):
            raise ValueError(f'{table} 存在目标不支持的字段：{set(columns) - set(types)}')
        query = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),
            sql.SQL(',').join(map(sql.Identifier, columns)), sql.SQL(',').join(sql.Placeholder() for _ in columns))
        cursor = source.execute(f'SELECT * FROM "{table}"')
        while rows := cursor.fetchmany(500):
            values = []
            for row in rows:
                record = []
                for key in columns:
                    value = row[key]
                    if value is not None and types[key] == 'boolean':
                        value = bool(value)
                    elif value is not None and types[key] in ('json', 'jsonb'):
                        value = Jsonb(json.loads(value))
                    record.append(value)
                values.append(record)
            with target.cursor() as cur:
                cur.executemany(query, values)
        copied = target.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()['n']
        if copied != count:
            raise ValueError(f'{table} 迁移前后行数不一致。')
        for key in columns:
            seq = target.execute('SELECT pg_get_serial_sequence(%s,%s) AS name', (table, key)).fetchone()['name']
            if seq:
                maximum = target.execute(sql.SQL('SELECT max({}) AS n FROM {}').format(
                    sql.Identifier(key), sql.Identifier(table))).fetchone()['n']
                target.execute('SELECT setval(%s,%s,%s)', (seq, maximum or 1, maximum is not None))
    return counts


def migrate(source_path, backup_dir, *, apply=False):
    cfg = load_config()
    with read_source(source_path) as source:
        counts = inspect_source(source)
        names = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        skipped = {table: source.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                   for table in sorted(LOCAL_ONLY_TABLES & names)}
        if not apply:
            return {'mode': 'dry-run', 'target': cfg.dbname, 'rows': counts, 'skipped_local_information': skipped}
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        directory = Path(backup_dir).expanduser() / f'pre-postgres-{stamp}'
        directory.mkdir(parents=True, mode=0o700)
        snapshot = directory / 'finresearch.sqlite'
        with sqlite3.connect(snapshot) as backup:
            source.backup(backup)
        snapshot.chmod(0o600)
    with read_source(snapshot) as source:
        counts = inspect_source(source)
        with psycopg.connect(cfg.dsn, row_factory=dict_row) as target:
            target.execute('SELECT pg_advisory_xact_lock(724946201)')
            copy_data(source, target, counts)
    report = {'mode': 'applied', 'target': cfg.dbname, 'source': str(Path(source_path).expanduser()),
              'backup': str(snapshot), 'rows': counts, 'skipped_local_information': skipped}
    (directory / 'migration.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT.parent / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--backup-dir', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    os.environ['FINANCE_STORE'] = 'postgres'
    print(json.dumps(migrate(args.source.expanduser(), args.backup_dir, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

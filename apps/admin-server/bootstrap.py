"""Explicit administrative bootstrap. Never invoked by desktop or server startup."""
import argparse
import os
from urllib.parse import unquote, urlparse

import psycopg
from psycopg import sql

from config import from_env
from repository import Repository
from backup_repository import BackupRepository


def bootstrap(admin_url, settings):
    settings.validate()
    parsed = urlparse(settings.database_url)
    role, password = unquote(parsed.username or ''), unquote(parsed.password or '')
    if not role or not password:
        raise ValueError('独立发布服务角色必须配置用户名和密码。')
    if role == unquote(urlparse(admin_url).username or ''):
        raise ValueError('发布服务必须使用不同于管理员的独立角色。')
    database = unquote(parsed.path.strip('/'))
    with psycopg.connect(admin_url, autocommit=True) as conn:
        if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Identifier(role), sql.Literal(password)))
        privileges = conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=%s', (role,)).fetchone()
        if any(privileges):
            raise ValueError('服务角色权限过高，拒绝继续初始化。')
        existing = conn.execute('SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s', (database,)).fetchone()
        if existing and existing[0] != role:
            raise ValueError('目标库已存在且不属于发布角色，不会接管现有数据库。')
        if not existing:
            conn.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(database), sql.Identifier(role)))
        conn.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(database)))
    with psycopg.connect(settings.database_url) as conn:
        conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
    Repository(settings.database_url).initialize()
    BackupRepository(settings.database_url).initialize()


if __name__ == '__main__':
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).with_name('.env'))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', required=True)
    parser.parse_args()
    bootstrap(os.environ.get('ADMIN_BOOTSTRAP_DATABASE_URL') or os.environ['RELEASE_ADMIN_DATABASE_URL'], from_env())
    print('admin-server 数据库及表结构已初始化。')

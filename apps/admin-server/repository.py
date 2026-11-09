"""PostgreSQL-only release metadata; all publication mutations share a DB lock."""
from contextlib import contextmanager
import uuid

import psycopg
from psycopg.rows import dict_row

DDL = '''
CREATE TABLE IF NOT EXISTS releases (
    id TEXT PRIMARY KEY, version TEXT NOT NULL UNIQUE,
    notes TEXT NOT NULL DEFAULT '', mandatory BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','withdrawn')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), published_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS release_artifacts (
    id TEXT PRIMARY KEY, release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    platform TEXT NOT NULL, arch TEXT NOT NULL, filename TEXT NOT NULL UNIQUE,
    size BIGINT NOT NULL CHECK(size > 0), sha512 TEXT NOT NULL,
    UNIQUE(release_id,platform,arch),
    CHECK((platform='darwin' AND arch IN ('arm64','x64')) OR (platform='win32' AND arch='x64')));
CREATE TABLE IF NOT EXISTS release_channels (
    name TEXT PRIMARY KEY, release_id TEXT REFERENCES releases(id));
INSERT INTO release_channels (name) VALUES ('stable') ON CONFLICT DO NOTHING;
'''


class Repository:
    def __init__(self, url):
        self.url = url

    @contextmanager
    def connection(self, mutation=False):
        with psycopg.connect(self.url, row_factory=dict_row) as conn:
            if mutation:
                conn.execute('SELECT pg_advisory_xact_lock(77010077)')
            yield conn

    def initialize(self):
        with self.connection(True) as conn:
            conn.execute(DDL)

    def all(self):
        with self.connection() as conn:
            rows = conn.execute('''SELECT r.*, c.release_id IS NOT NULL AS latest FROM releases r
                LEFT JOIN release_channels c ON c.release_id=r.id AND c.name='stable'
                ORDER BY r.created_at DESC''').fetchall()
            assets = conn.execute('SELECT * FROM release_artifacts ORDER BY platform,arch').fetchall()
        for row in rows:
            row['artifacts'] = [a for a in assets if a['release_id'] == row['id']]
        return rows

    def create(self, version, notes, mandatory):
        identifier = uuid.uuid4().hex
        try:
            with self.connection(True) as conn:
                conn.execute('INSERT INTO releases (id,version,notes,mandatory) VALUES (%s,%s,%s,%s)',
                             (identifier, version, notes, mandatory))
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError('该版本号已存在。') from exc
        return identifier

    def edit(self, identifier, notes, mandatory):
        with self.connection(True) as conn:
            changed = conn.execute('UPDATE releases SET notes=%s,mandatory=%s WHERE id=%s RETURNING id',
                                   (notes, mandatory, identifier)).fetchone()
            if not changed:
                raise ValueError('版本不存在。')

    def artifact(self, identifier):
        with self.connection() as conn:
            return conn.execute('''SELECT a.*,r.version,r.notes,r.mandatory,r.published_at FROM release_artifacts a
                JOIN releases r ON r.id=a.release_id WHERE a.id=%s AND r.status='published' ''', (identifier,)).fetchone()

    def add_artifact(self, release_id, platform, arch, filename, size, sha512):
        with self.connection(True) as conn:
            row = conn.execute('SELECT status FROM releases WHERE id=%s FOR UPDATE', (release_id,)).fetchone()
            if not row or row['status'] != 'draft':
                raise ValueError('仅草稿允许上传安装包；已发布版本必须新建版本。')
            old = conn.execute('SELECT filename FROM release_artifacts WHERE release_id=%s AND platform=%s AND arch=%s',
                               (release_id, platform, arch)).fetchone()
            conn.execute('''INSERT INTO release_artifacts (id,release_id,platform,arch,filename,size,sha512)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(release_id,platform,arch) DO UPDATE SET
                id=EXCLUDED.id,filename=EXCLUDED.filename,size=EXCLUDED.size,sha512=EXCLUDED.sha512''',
                (uuid.uuid4().hex, release_id, platform, arch, filename, size, sha512))
        return old['filename'] if old else None

    def publish(self, identifier):
        with self.connection(True) as conn:
            row = conn.execute('SELECT * FROM releases WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
            if not row or row['status'] != 'draft':
                raise ValueError('仅草稿可以发布。')
            if not conn.execute('SELECT 1 FROM release_artifacts WHERE release_id=%s', (identifier,)).fetchone():
                raise ValueError('请至少上传一个平台的安装包。')
            conn.execute("UPDATE releases SET status='published',published_at=NOW() WHERE id=%s", (identifier,))

    def latest(self, platform, arch):
        with self.connection() as conn:
            return conn.execute('''SELECT r.*,a.id AS artifact_id,a.filename,a.size,a.sha512,a.platform,a.arch
                FROM release_channels c JOIN releases r ON r.id=c.release_id
                JOIN release_artifacts a ON a.release_id=r.id
                WHERE c.name='stable' AND r.status='published' AND a.platform=%s AND a.arch=%s''', (platform, arch)).fetchone()

    def set_latest(self, identifier):
        with self.connection(True) as conn:
            if identifier and not conn.execute("SELECT 1 FROM releases WHERE id=%s AND status='published'", (identifier,)).fetchone():
                raise ValueError('只有已发布版本才能设置为最新版本。')
            conn.execute("UPDATE release_channels SET release_id=%s WHERE name='stable'", (identifier or None,))

    def withdraw(self, identifier, replacement):
        with self.connection(True) as conn:
            if replacement == identifier:
                raise ValueError('替代版本不能是正在撤回的版本。')
            if replacement and not conn.execute("SELECT 1 FROM releases WHERE id=%s AND status='published'", (replacement,)).fetchone():
                raise ValueError('替代版本必须已发布。')
            changed = conn.execute("UPDATE releases SET status='withdrawn' WHERE id=%s AND status='published' RETURNING id", (identifier,)).fetchone()
            if not changed:
                raise ValueError('该版本未发布。')
            conn.execute("UPDATE release_channels SET release_id=%s WHERE release_id=%s", (replacement or None, identifier))

    def delete_draft(self, identifier):
        with self.connection(True) as conn:
            row = conn.execute('SELECT status FROM releases WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
            if not row or row['status'] != 'draft':
                raise ValueError('仅草稿可以删除。')
            files = conn.execute('SELECT filename FROM release_artifacts WHERE release_id=%s', (identifier,)).fetchall()
            conn.execute('DELETE FROM releases WHERE id=%s', (identifier,))
        return [r['filename'] for r in files]

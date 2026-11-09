from repository import Repository

DDL = '''CREATE TABLE IF NOT EXISTS client_backups (
    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, email TEXT NOT NULL, device_id TEXT NOT NULL,
    client_version BIGINT NOT NULL CHECK(client_version>0), created_at TIMESTAMPTZ NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), kind TEXT NOT NULL,
    name TEXT NOT NULL, storage_name TEXT NOT NULL UNIQUE,
    size BIGINT NOT NULL CHECK(size>0), sha256 TEXT NOT NULL,
    UNIQUE(owner_id,device_id,client_version));
CREATE INDEX IF NOT EXISTS client_backups_owner_device ON client_backups(owner_id,device_id,client_version DESC);
'''


class BackupRepository(Repository):
    def initialize(self):
        with self.connection(True) as conn:
            conn.execute(DDL)

    def list_for(self, owner, device):
        with self.connection() as conn:
            return conn.execute('SELECT * FROM client_backups WHERE owner_id=%s AND device_id=%s ORDER BY client_version DESC LIMIT 500', (owner,device)).fetchall()

    def get_for(self, owner, device, identifier):
        with self.connection() as conn:
            return conn.execute('SELECT * FROM client_backups WHERE owner_id=%s AND device_id=%s AND id=%s', (owner,device,identifier)).fetchone()

    def register(self, row):
        with self.connection(True) as conn:
            existing = conn.execute('SELECT * FROM client_backups WHERE id=%s', (row['id'],)).fetchone()
            if existing:
                if any(existing[key] != row[key] for key in ('owner_id','device_id','client_version','sha256','size')):
                    raise ValueError('该备份编号已存在且内容不同，请重新创建备份。')
                return existing
            collision = conn.execute('SELECT 1 FROM client_backups WHERE owner_id=%s AND device_id=%s AND client_version=%s',
                (row['owner_id'],row['device_id'],row['client_version'])).fetchone()
            if collision:
                raise ValueError('备份序号冲突，请刷新记录后重新备份。')
            columns = ('id','owner_id','email','device_id','client_version','created_at','kind','name','storage_name','size','sha256')
            return conn.execute(f'INSERT INTO client_backups ({",".join(columns)}) VALUES ({",".join("%s" for _ in columns)}) RETURNING *', tuple(row[c] for c in columns)).fetchone()

    def admin_list(self):
        with self.connection() as conn:
            return conn.execute('SELECT * FROM client_backups ORDER BY uploaded_at DESC LIMIT 500').fetchall()

"""Local backup history inside office.sqlite; independent from restorable asset rows."""
import argparse
from contextlib import closing
import json
import sqlite3

DDL='''CREATE TABLE IF NOT EXISTS local_backup_records (
    id TEXT PRIMARY KEY, user_key TEXT NOT NULL, device_id TEXT NOT NULL,
    version INTEGER NOT NULL, data TEXT NOT NULL,
    UNIQUE(user_key,device_id,version));'''


def command(database,operation,payload):
    with closing(sqlite3.connect(database,timeout=30)) as conn:
        conn.execute(DDL)
        conn.commit()
        key,device=payload['userKey'],payload['deviceId']
        if operation=='reserve':
            conn.execute('BEGIN IMMEDIATE')
            version=conn.execute('SELECT coalesce(max(version),0)+1 FROM local_backup_records WHERE user_key=? AND device_id=?',(key,device)).fetchone()[0]
            record={**payload,'version':version,'state':'creating'}
            conn.execute('INSERT INTO local_backup_records VALUES (?,?,?,?,?)',(record['id'],key,device,version,json.dumps(record)))
            conn.commit()
            return record
        if operation=='save':
            existing=conn.execute('SELECT user_key,device_id,version FROM local_backup_records WHERE id=?',(payload['id'],)).fetchone()
            if existing and existing[:2]!=(key,device):
                raise ValueError('备份编号已属于其他账号或设备，已拒绝覆盖。')
            if existing and existing[2]!=payload['version']:
                raise ValueError('已存在的备份版本序号不能修改。')
            previous=conn.execute('SELECT data FROM local_backup_records WHERE id=? AND user_key=? AND device_id=?',(payload['id'],key,device)).fetchone()
            record={**(json.loads(previous[0]) if previous else {}),**payload}
            conflict=conn.execute('SELECT id FROM local_backup_records WHERE user_key=? AND device_id=? AND version=?',(key,device,record['version'])).fetchone()
            if conflict and conflict[0]!=record['id']:
                raise ValueError('本地与服务器的备份编号发生冲突，已停止处理。')
            conn.execute('''INSERT INTO local_backup_records VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data
                WHERE local_backup_records.user_key=excluded.user_key AND local_backup_records.device_id=excluded.device_id''',
                (record['id'],key,device,record['version'],json.dumps(record)))
            conn.commit()
            return record
        if operation=='list':
            return [json.loads(row[0]) for row in conn.execute('SELECT data FROM local_backup_records WHERE user_key=? AND device_id=? ORDER BY version DESC',(key,device))]
        raise ValueError('不支持的备份记录操作。')


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True)
    parser.add_argument('--operation',choices=('reserve','save','list'),required=True)
    parser.add_argument('--payload',required=True)
    args=parser.parse_args(argv)
    print(json.dumps(command(args.database,args.operation,json.loads(args.payload))))


if __name__=='__main__':
    main()

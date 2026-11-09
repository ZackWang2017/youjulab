import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

import sqlite_vec
from user_backup import OWNED, export_user, restore_user


class UserBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.office, self.vectors, self.files = self.root/'office.sqlite', self.root/'vectors.sqlite', self.root/'files'
        self.out = self.root/'export'
        with sqlite3.connect(self.office) as conn:
            for table in ['users', *OWNED]:
                conn.execute(f'CREATE TABLE {table} (id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            for uid in ('a', 'b'):
                self.put(conn, 'users', uid, dict(id=uid, email=uid+'@example.com',password_hash='secret-'+uid,access_token='token-'+uid))
                self.put(conn, 'projects', uid+'p', dict(id=uid+'p',owner_id=uid))
                pdf = self.files/uid/(uid+'p')/(uid+'pdf.pdf')
                pdf.parent.mkdir(parents=True)
                pdf.write_bytes(('PDF-'+uid).encode())
                self.put(conn,'assets',uid+'pdf',dict(id=uid+'pdf',owner_id=uid,project_id=uid+'p',kind='pdf',file_path=str(pdf),content='original-'+uid))
                self.put(conn,'user_model_configs',uid,dict(user_id=uid,model='model-'+uid))
        with sqlite3.connect(self.vectors) as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.executescript('''CREATE TABLE pdf_index (document_id TEXT PRIMARY KEY,user_id TEXT,status TEXT);
                CREATE TABLE pdf_chunks (document_id TEXT,chunk_id TEXT,page INT,ord INT,text TEXT);
                CREATE TABLE pdf_layout_blocks (document_id TEXT,text TEXT);
                CREATE VIRTUAL TABLE pdf_chunk_vec USING vec0(document_id TEXT,chunk_id TEXT,embedding float[2]);''')
            for uid in ('a','b'):
                conn.execute('INSERT INTO pdf_index VALUES (?,?,?)',(uid+'pdf',uid,'ready'))
                conn.execute('INSERT INTO pdf_chunks VALUES (?,?,1,0,?)',(uid+'pdf','chunk', 'text-'+uid))
                conn.execute('INSERT INTO pdf_layout_blocks VALUES (?,?)',(uid+'pdf','layout-'+uid))
                conn.execute('INSERT INTO pdf_chunk_vec (document_id,chunk_id,embedding) VALUES (?,?,?)',(uid+'pdf','chunk',struct.pack('2f',1,0)))

    @staticmethod
    def put(conn,table,key,value):
        conn.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?)',(key,json.dumps(value)))

    def export(self):
        export_user(self.office,self.vectors,self.files,'a@example.com','remote-a',self.out)
        return json.loads((self.out/'user-data.json').read_text())

    def restore(self):
        return restore_user(self.office,self.vectors,self.files,'a@example.com','remote-a',self.out)

    def test_legacy_path_prefers_current_userdata(self):
        legacy = self.root/'old-root/a/ap/apdf.pdf'
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b'old-pdf')
        with sqlite3.connect(self.office) as conn:
            row = json.loads(conn.execute("SELECT data FROM assets WHERE id='apdf'").fetchone()[0])
            row['file_path'] = str(legacy)
            self.put(conn, 'assets', 'apdf', row)
        self.export()
        self.assertEqual((self.out/'office-files/a/ap/apdf.pdf').read_bytes(), b'PDF-a')
        (self.files/'a/ap/apdf.pdf').unlink()
        self.export()
        self.assertEqual((self.out/'office-files/a/ap/apdf.pdf').read_bytes(), b'old-pdf')

    def test_foreign_legacy_path_rejected(self):
        (self.files/'a/ap/apdf.pdf').unlink()
        with sqlite3.connect(self.office) as conn:
            row = json.loads(conn.execute("SELECT data FROM assets WHERE id='apdf'").fetchone()[0])
            row['file_path'] = str(self.files/'b/bp/bpdf.pdf')
            self.put(conn, 'assets', 'apdf', row)
        with self.assertRaisesRegex(ValueError, '归属路径异常'):
            self.export()

    def test_export_never_contains_other_user_or_credentials(self):
        payload = self.export()
        text = json.dumps(payload)
        self.assertNotIn('secret-',text)
        self.assertNotIn('token-',text)
        self.assertNotIn('original-b',text)
        self.assertNotIn('text-b',text)
        self.assertNotIn('model-b',text)
        self.assertEqual(list((self.out/'office-files').iterdir()),[self.out/'office-files/a'])
        self.assertEqual(payload['tables']['user_model_configs'][0]['user_id'],'a')

    def test_restore_only_changes_selected_user(self):
        self.export()
        with sqlite3.connect(self.office) as conn:
            for uid in ('a','b'):
                row=json.loads(conn.execute('SELECT data FROM assets WHERE id=?',(uid+'pdf',)).fetchone()[0])
                row['content']='updated-'+uid
                self.put(conn,'assets',uid+'pdf',row)
            self.put(conn,'users','a',dict(id='a',email='a@example.com',password_hash='new-password',access_token='new-token'))
        (self.files/'a/ap/apdf.pdf').write_bytes(b'changed-a')
        (self.files/'b/bp/bpdf.pdf').write_bytes(b'changed-b')
        self.restore()
        with sqlite3.connect(self.office) as conn:
            values={r[0]:json.loads(r[1]) for r in conn.execute('SELECT * FROM assets')}
            self.assertEqual(values['apdf']['content'],'original-a')
            self.assertEqual(values['bpdf']['content'],'updated-b')
            local=json.loads(conn.execute("SELECT data FROM users WHERE id='a'").fetchone()[0])
            self.assertEqual(local['access_token'],'new-token')
            self.assertEqual(local['password_hash'],'new-password')
        self.assertEqual((self.files/'a/ap/apdf.pdf').read_bytes(),b'PDF-a')
        self.assertEqual((self.files/'b/bp/bpdf.pdf').read_bytes(),b'changed-b')
        with sqlite3.connect(self.vectors) as conn:
            conn.enable_load_extension(True);sqlite_vec.load(conn)
            self.assertEqual(conn.execute('SELECT count(*) FROM pdf_chunk_vec').fetchone()[0],2)
            self.assertEqual(conn.execute("SELECT text FROM pdf_chunks WHERE document_id='bpdf'").fetchone()[0],'text-b')

    def test_foreign_rows_abort_without_mutation(self):
        payload=self.export()
        payload['tables']['assets'][0]['owner_id']='b'
        (self.out/'user-data.json').write_text(json.dumps(payload))
        before=self.office.read_bytes()
        with self.assertRaisesRegex(ValueError,'其他账号'):
            self.restore()
        self.assertEqual(before,self.office.read_bytes())

    def test_wrong_remote_identity_is_rejected(self):
        self.export()
        with self.assertRaisesRegex(ValueError,'身份不一致'):
            restore_user(self.office,self.vectors,self.files,'a@example.com','remote-b',self.out)

    def test_foreign_primary_key_collision_is_rejected(self):
        payload=self.export()
        payload['tables']['projects'][0]['__backup_key']='bp'
        (self.out/'user-data.json').write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError,'其他用户'):
            self.restore()

    def test_foreign_vectors_are_rejected(self):
        payload=self.export()
        payload['vectors']['rows']['pdf_chunks'][0]['document_id']='bpdf'
        (self.out/'user-data.json').write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError,'其他账号'):
            self.restore()

    def test_other_users_attachment_path_is_rejected_without_writes(self):
        payload=self.export()
        payload['tables']['assets'][0]['file_path']='office-files/b/bp/bpdf.pdf'
        (self.out/'user-data.json').write_text(json.dumps(payload))
        before=self.office.read_bytes()
        with self.assertRaisesRegex(ValueError,'路径不安全'):
            self.restore()
        self.assertEqual(before,self.office.read_bytes())

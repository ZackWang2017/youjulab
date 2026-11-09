import asyncio
import hashlib
import io
import json
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
import tarfile
import tempfile
import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient
from config import Settings
from server import create_app
from test_server import FakeRepository


class FakeBackups:
    def __init__(self):self.rows={}
    def register(self,row):
        old=self.rows.get(row['id'])
        if old:
            if any(old[k]!=row[k] for k in ('owner_id','device_id','sha256','client_version','size')):raise ValueError('备份编号冲突')
            return old
        if any(r['owner_id']==row['owner_id'] and r['device_id']==row['device_id'] and r['client_version']==row['client_version'] for r in self.rows.values()):raise ValueError('序号冲突')
        self.rows[row['id']]={**row,'uploaded_at':datetime.now(timezone.utc)}
        return self.rows[row['id']]
    def list_for(self,owner,device):return sorted([r for r in self.rows.values() if r['owner_id']==owner and r['device_id']==device],key=lambda r:r['client_version'],reverse=True)
    def get_for(self,owner,device,bid):return next((r for r in self.list_for(owner,device) if r['id']==bid),None)
    def admin_list(self):return list(self.rows.values())


class BackupApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.repo=FakeBackups()
        self.bid='a'*32;self.device='d'*32
        async def auth(header):
            token=header.removeprefix('Bearer ')
            if token not in ('alice','bob'):raise HTTPException(401)
            return {'id':token,'email':token+'@example.com'}
        settings=Settings('postgresql://role:pw@localhost/admin-server',self.root/'packages','x'*40,
            'pbkdf2_sha256$600000$unused$unused',public_url='https://admin.example.com',backup_root=self.root/'backups',max_backup=1024**2)
        self.client=TestClient(create_app(settings,FakeRepository(),self.repo,auth),base_url='https://admin.example.com')
        self.addCleanup(self.client.close)

    def archive(self,bid=None,version=1,owner='alice'):
        manifest={'version':2,'scope':'local-user','account':{'accountId':owner,'email':owner+'@example.com'},
            'backupId':bid or self.bid,'deviceId':self.device,'clientVersion':version}
        raw=json.dumps(manifest).encode();output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w:gz') as archive:
            entry=tarfile.TarInfo('manifest.json');entry.size=len(raw);archive.addfile(entry,io.BytesIO(raw))
        return output.getvalue()

    def upload(self,bid=None,version=1,owner='alice',archive_owner=None):
        data=self.archive(bid,version,archive_owner or owner)
        return self.client.put('/api/v1/backups/'+(bid or self.bid),params={'device_id':self.device,'version':version,
            'created_at':datetime.now(timezone.utc).isoformat(),'kind':'manual','name':'youjulab-backup-test.tar.gz'},
            content=data,headers={'Authorization':'Bearer '+owner,'X-Content-SHA256':hashlib.sha256(data).hexdigest()})

    def test_upload_list_download_idempotent_and_owner_isolation(self):
        first=self.upload();self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(self.upload().status_code,200)
        record=first.json()['record'];self.assertNotIn('storage_name',record)
        self.assertEqual(len(list((self.root/'backups').rglob('*.tar.gz'))),1)
        path='/api/v1/backups?device_id='+self.device
        self.assertEqual(len(self.client.get(path,headers={'Authorization':'Bearer alice'}).json()['records']),1)
        self.assertEqual(self.client.get(path,headers={'Authorization':'Bearer bob'}).json()['records'],[])
        download='/api/v1/backups/'+self.bid+'/download?device_id='+self.device
        self.assertEqual(self.client.get(download,headers={'Authorization':'Bearer bob'}).status_code,404)
        response=self.client.get(download,headers={'Authorization':'Bearer alice'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(hashlib.sha256(response.content).hexdigest(),record['sha256'])

    def test_foreign_manifest_is_rejected_and_not_saved(self):
        self.assertEqual(self.upload(archive_owner='bob').status_code,403)
        self.assertFalse(self.repo.rows)
        self.assertEqual(list((self.root/'backups').rglob('*.upload')),[])
        self.assertEqual(self.client.get('/api/v1/backups?device_id='+self.device).status_code,401)

    def test_versions_not_client_clock_determine_latest(self):
        self.assertEqual(self.upload().status_code,200)
        self.assertEqual(self.upload('b'*32,2).status_code,200)
        rows=self.client.get('/api/v1/backups?device_id='+self.device,headers={'Authorization':'Bearer alice'}).json()['records']
        self.assertEqual([r['client_version'] for r in rows],[2,1])
        self.assertEqual(self.upload('c'*32,2).status_code,409)

import base64
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from config import Settings
from repository import Repository
from security import password_hash, verify_password
from server import create_app, validate_version
from bootstrap import bootstrap


class FakeRepository:
    def __init__(self):
        self.rows = []
    def all(self): return self.rows
    def create(self, version, notes, mandatory):
        if any(r['version'] == version for r in self.rows): raise ValueError('该版本号已存在。')
        self.rows.append(dict(id='release', version=version, notes=notes, mandatory=mandatory,
            status='draft', latest=False, created_at=datetime.now(timezone.utc), published_at=None, artifacts=[]))
    def add_artifact(self, release_id, platform, arch, filename, size, sha512):
        row=self.rows[0]
        if row['status']!='draft': raise ValueError('仅草稿允许上传安装包。')
        row['artifacts'].append(dict(id='artifact',release_id=release_id,platform=platform,arch=arch,filename=filename,size=size,sha512=sha512))
    def publish(self, identifier):
        if not self.rows[0]['artifacts']: raise ValueError('请至少上传一个平台的安装包。')
        self.rows[0].update(status='published',published_at=datetime.now(timezone.utc))
    def set_latest(self, identifier):
        for row in self.rows: row['latest']=row['id']==identifier
    def latest(self, platform, arch):
        for row in self.rows:
            if row['latest'] and row['status']=='published':
                for a in row['artifacts']:
                    if (a['platform'],a['arch'])==(platform,arch): return {**row, **a, 'id':row['id'],'artifact_id':a['id']}
    def artifact(self, identifier):
        for row in self.rows:
            for a in row['artifacts']:
                if a['id']==identifier and row['status']=='published': return {**row,**a}
    def edit(self, identifier, notes, mandatory): self.rows[0].update(notes=notes,mandatory=mandatory)
    def withdraw(self, identifier, replacement): self.rows[0].update(status='withdrawn',latest=False)
    def delete_draft(self, identifier):
        self.rows.clear();return []


class ReleaseServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.hashed=password_hash('test-password-for-release')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.settings=Settings('postgresql://release:private@localhost/release-server',Path(self.temp.name),
            'secret-session-key-at-least-32-characters',self.hashed,public_url='https://updates.example.com')
        self.repo=FakeRepository()
        self.client=TestClient(create_app(self.settings,self.repo),base_url='https://updates.example.com')
        self.addCleanup(self.client.close)

    def login(self):
        html=self.client.get('/login').text
        token=re.search(r'name="csrf" type="hidden" value="([^"]+)"',html)[1]
        response=self.client.post('/login',data=dict(csrf=token,username='admin',password='test-password-for-release'))
        self.assertEqual(response.status_code,200)
        return re.search(r'name="csrf"[^>]*value="([^"]+)"',response.text)[1]

    def test_public_release_downloads(self):
        self.assertIn('暂未发布版本', self.client.get('/release').text)
        for version, status in [('1.9.0', 'published'), ('1.10.0', 'published'), ('9.0.0', 'draft'), ('8.0.0', 'withdrawn')]:
            self.repo.create(version, '<script>unsafe</script>', False)
            self.repo.rows[-1].update(status=status, latest=version=='1.10.0', artifacts=[dict(
                id='public-artifact', platform='darwin', arch='arm64', filename='public.dmg', size=4)])
        response = self.client.get('/release')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('set-cookie', response.headers)
        self.assertLess(response.text.index('v1.10.0'), response.text.index('v1.9.0'))
        self.assertNotIn('v9.0.0', response.text)
        self.assertNotIn('v8.0.0', response.text)
        self.assertIn('&lt;script&gt;', response.text)
        self.assertIn('/packages/public-artifact/public.dmg', response.text)
        Path(self.temp.name, 'public.dmg').write_bytes(b'test')
        # FakeRepository uses the same ID for all fixtures; the first published record matches.
        self.repo.rows[0]['artifacts'][0]['id'] = 'public-artifact'
        self.assertEqual(self.client.get('/packages/public-artifact/public.dmg').content, b'test')

    def test_root_redirects_by_login_state(self):
        response = self.client.get('/', follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/login')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.login()
        response = self.client.get('/', follow_redirects=False)
        self.assertEqual(response.headers['location'], '/admin')

    def test_login_csrf_and_rate_limit(self):
        self.assertEqual(self.client.post('/admin/releases',data={}).status_code,401)
        self.assertEqual(self.client.post('/login',data={'username':'admin'}).status_code,403)
        token=re.search(r'name="csrf" type="hidden" value="([^"]+)"',self.client.get('/login').text)[1]
        for _ in range(5):
            self.client.post('/login',data=dict(csrf=token,username='admin',password='wrong'))
        self.assertEqual(self.client.post('/login',data=dict(csrf=token,password='wrong')).status_code,429)

    def test_password_and_database_boundaries(self):
        self.assertTrue(verify_password('test-password-for-release',self.hashed))
        self.assertFalse(verify_password('wrong',self.hashed))
        with self.assertRaises(ValueError): replace(self.settings,database_url='postgresql://localhost/finresearch').validate()
        with self.assertRaises(ValueError): replace(self.settings,public_url='http://updates.example.com').validate()
        for value in ('1.0','01.0.0','1.0.0-beta','1.0.0; DROP TABLE releases'):
            with self.assertRaises(ValueError): validate_version(value)

    def test_publish_upload_latest_and_windows_feed(self):
        csrf=self.login()
        self.client.post('/admin/releases',data=dict(csrf=csrf,version='1.2.3',notes='<script>bad</script>',mandatory='on'))
        self.assertIn('&lt;script&gt;',self.client.get('/admin').text)
        self.client.post('/admin/upload/release',data=dict(csrf=csrf,platform='win32',arch='x64'),
                         files={'package':('../../fake.exe',b'MZ-test-package','application/octet-stream')})
        asset=self.repo.rows[0]['artifacts'][0]
        self.assertNotIn('fake',asset['filename'])
        self.assertEqual(self.client.get('/packages/artifact/'+asset['filename']).status_code,404)
        self.client.post('/admin/releases/release/publish',data={'csrf':csrf})
        self.client.post('/admin/releases/release/latest',data={'csrf':csrf})
        result=self.client.get('/api/v1/releases/latest?platform=win32&arch=x64').json()['release']
        self.assertEqual(result['version'],'1.2.3');self.assertTrue(result['mandatory'])
        self.assertEqual(result['sha512'],base64.b64encode(hashlib.sha512(b'MZ-test-package').digest()).decode())
        self.assertIn('version: "1.2.3"',self.client.get('/updates/win32/x64/release/latest.yml').text)
        self.assertEqual(self.client.get('/packages/artifact/'+asset['filename']).content,b'MZ-test-package')
        self.assertEqual(self.client.get('/api/v1/releases/latest?platform=darwin&arch=arm64').json(),{'release':None})
        self.client.post('/admin/upload/release',data=dict(csrf=csrf,platform='win32',arch='x64'),files={'package':('other.exe',b'MZ-other')})
        self.assertEqual(len(list(self.settings.files.iterdir())),1)
        self.client.post('/admin/releases/release/withdraw',data=dict(csrf=csrf,replacement=''))
        self.assertEqual(self.client.get('/api/v1/releases/latest?platform=win32&arch=x64').json(),{'release':None})
        self.assertEqual(self.client.get('/packages/artifact/'+asset['filename']).status_code,404)

    def test_upload_limit_and_platform_validation(self):
        self.client.close()
        self.settings=replace(self.settings,max_upload=4)
        self.client=TestClient(create_app(self.settings,self.repo),base_url='https://updates.example.com')
        csrf=self.login()
        self.repo.create('1.0.0','notes',False)
        response=self.client.post('/admin/upload/release',data=dict(csrf=csrf,platform='darwin',arch='arm64'),files={'package':('file.dmg',b'12345')})
        self.assertEqual(response.status_code,413)
        self.assertEqual(list(self.settings.files.glob('*')),[])
        self.assertEqual(self.client.get('/api/v1/releases/latest?platform=win32&arch=arm64').status_code,400)

    def test_repository_mutations_take_transaction_lock(self):
        connection=MagicMock()
        connection.execute.return_value.fetchone.return_value={'status':'draft'}
        context=MagicMock();context.__enter__.return_value=connection
        with patch('repository.psycopg.connect',return_value=context):
            Repository(self.settings.database_url).publish('release')
        statements=[str(c.args[0]) for c in connection.execute.call_args_list]
        self.assertIn('pg_advisory_xact_lock',statements[0])
        self.assertTrue(any('FOR UPDATE' in q for q in statements))
        context.__exit__.assert_called_once()

    def test_bootstrap_quotes_database_name_and_rejects_admin_reuse(self):
        with self.assertRaises(ValueError):
            bootstrap('postgresql://release:private@localhost/postgres',self.settings)
        connection=MagicMock()
        connection.execute.return_value.fetchone.side_effect=[None,(False,False,False),None]
        context=MagicMock();context.__enter__.return_value=connection
        with patch('bootstrap.psycopg.connect',return_value=context), patch('bootstrap.Repository.initialize') as initialize:
            bootstrap('postgresql://administrator:private@localhost/postgres',self.settings)
            initialize.assert_called_once()
        composed=[call.args[0].as_string() for call in connection.execute.call_args_list if hasattr(call.args[0],'as_string')]
        self.assertTrue(any('CREATE DATABASE "release-server" OWNER "release"' in q for q in composed))

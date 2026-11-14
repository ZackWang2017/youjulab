import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from fastapi.testclient import TestClient
import screen_lock as lock
from server import create_app
from store import MemoryStore


class ScreenLockTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {'OFFICE_SQLITE_PATH': str(self.root / 'office.sqlite')})
        env.start()
        self.addCleanup(env.stop)
        lock._attempts.clear()

    def save(self, **kwargs):
        return lock.update_preferences('owner', background='stars', **kwargs)

    def test_default_and_passwordless_unlock(self):
        self.assertEqual(lock.preferences('owner'), {'background': 'stars', 'password_enabled': False})
        self.assertEqual(lock.unlock('owner', ''), {'ok': True})

    def test_password_hash_permissions_and_user_isolation(self):
        self.save(password_enabled=True, new_password='secret-pass')
        raw = lock._path('owner').read_text()
        self.assertNotIn('secret-pass', raw)
        self.assertEqual(lock._path('owner').stat().st_mode & 0o777, 0o600)
        self.assertTrue(lock.preferences('owner')['password_enabled'])
        self.assertFalse(lock.preferences('other')['password_enabled'])
        self.assertEqual(lock.unlock('owner', 'secret-pass'), {'ok': True})
        with self.assertRaises(HTTPException):
            lock.unlock('owner', 'wrong')

    def test_change_background_preserves_password_and_disable_requires_password(self):
        self.save(password_enabled=True, new_password='secret')
        lock.update_preferences('owner', background='lake', password_enabled=True)
        self.assertEqual(lock.unlock('owner', 'secret'), {'ok': True})
        with self.assertRaises(HTTPException):
            self.save(password_enabled=False)
        self.save(password_enabled=True, current_password='secret', new_password='new-secret')
        with self.assertRaises(HTTPException):
            lock.unlock('owner', 'secret')
        self.save(password_enabled=False, current_password='new-secret')
        self.assertNotIn('digest', json.loads(lock._path('owner').read_text()))
        self.assertFalse(lock.preferences('owner')['password_enabled'])

    def test_validation_corruption_and_throttling(self):
        with self.assertRaises(HTTPException):
            self.save(password_enabled=True, new_password='123')
        with self.assertRaises(HTTPException):
            lock.update_preferences('owner', background='bad', password_enabled=False)
        self.save(password_enabled=True, new_password='secret')
        for _ in range(5):
            with self.assertRaises(HTTPException): lock.unlock('owner', 'bad')
        with self.assertRaises(HTTPException) as error:
            lock.unlock('owner', 'secret')
        self.assertEqual(error.exception.status_code, 429)
        lock._path('owner').write_text('invalid')
        with self.assertRaises(ValueError): lock.preferences('owner')

    def test_authenticated_routes(self):
        with TestClient(create_app(MemoryStore())) as client:
            self.assertEqual(client.get('/local/screen-lock').status_code, 401)
            auth = client.post('/auth/register', json={'email': 'lock@example.com', 'password': 'secret1'}).json()
            headers = {'Authorization': f"Bearer {auth['token']}"}
            self.assertEqual(client.get('/local/screen-lock', headers=headers).json()['password_enabled'], False)
            response = client.put('/local/screen-lock', headers=headers, json={
                'background': 'orbit', 'password_enabled': True, 'new_password': '123456'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn('digest', response.json())
            self.assertEqual(client.post('/local/screen-lock/unlock', headers=headers, json={'password': 'bad'}).status_code, 403)
            self.assertEqual(client.post('/local/screen-lock/unlock', headers=headers, json={'password': '123456'}).status_code, 200)

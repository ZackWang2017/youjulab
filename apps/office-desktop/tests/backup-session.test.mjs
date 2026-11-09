import test from 'node:test';
import assert from 'node:assert/strict';
import { BackupSession, backupUserKey } from '../backup-session.mjs';

test('backup identity comes from verified remote session', async () => {
  const session = new BackupSession(() => 'http://server', async (_url, options) => {
    assert.equal(options.headers.Authorization, 'Bearer jwt');
    return { ok: true, json: async () => ({ id: 'remote-a', email: 'A@example.com' }) };
  });
  session.set('jwt');
  assert.equal((await session.current()).email, 'a@example.com');
  session.set(null);
  await assert.rejects(session.current(), /请先登录/);
});
test('server switch invalidates backup session and users have separate folders', async () => {
  let base = 'http://server';
  const session = new BackupSession(() => base);
  session.set('jwt');
  base = 'http://other';
  await assert.rejects(session.current(), /服务器已切换/);
  const a = { id: 'a', email: 'a@example.com', server: 'http://server' };
  assert.notEqual(backupUserKey(a), backupUserKey({ ...a, id: 'b' }));
  assert.notEqual(backupUserKey(a), backupUserKey({ ...a, server: 'http://other' }));
});

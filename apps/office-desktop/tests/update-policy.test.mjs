import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { checkRelease, downloadMac, installVerifiedUpdate, newer, updateBase, validateRelease, verifyFile } from '../update-policy.mjs';

const base = 'https://updates.example.com';
const bytes = Buffer.from('test package');
const release = { id: 'r', version: '1.2.0', notes: 'notes', mandatory: true, platform: 'darwin', arch: 'arm64',
  download_url: base + '/packages/a/pkg.dmg', update_feed_url: base + '/updates/darwin/arm64/r',
  size: bytes.length, sha512: createHash('sha512').update(bytes).digest('base64') };
const options = { base, version: '1.0.0', platform: 'darwin', arch: 'arm64' };

test('LAN HTTP supports local testing while public HTTP and foreign downloads remain blocked', () => {
  const local = 'http://192.168.0.5:8200';
  assert.equal(updateBase(local + '/'), local);
  assert.equal(updateBase('http://localhost:8200'), 'http://localhost:8200');
  for (const address of ['http://example.com', 'http://8.8.8.8', 'http://192.168.0.5@evil.com', 'ftp://192.168.0.5']) {
    assert.throws(() => updateBase(address));
  }
  const localRelease = { ...release, download_url: local + '/packages/a/pkg.dmg', update_feed_url: local + '/updates/darwin/arm64/r' };
  assert.equal(validateRelease(localRelease, local, 'darwin', 'arm64'), localRelease);
  assert.throws(() => validateRelease({ ...localRelease, download_url: 'http://192.168.0.6/file' }, local, 'darwin', 'arm64'));
});

test('Windows install runs only after checksum and service cleanup, and cleanup failure aborts', async t => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'youju-install-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'installer.exe');
  await fs.writeFile(file, bytes);
  const events = [];
  await installVerifiedUpdate(file, release, { stopServices: async () => events.push('stop'), install: () => events.push('install') });
  assert.deepEqual(events, ['stop', 'install']);
  await assert.rejects(installVerifiedUpdate(file, release, {
    stopServices: async () => { throw Error('cleanup failed'); }, install: () => assert.fail('must not install'),
  }), /cleanup failed/);
  await fs.writeFile(file, 'bad');
  await assert.rejects(installVerifiedUpdate(file, release, {
    stopServices: async () => assert.fail('must verify first'), install: () => assert.fail('must not install'),
  }), /校验/);
});

test('strict semantic comparison never downgrades', () => {
  assert.equal(newer({ version: '1.10.0' }, '1.9.0'), true);
  assert.equal(newer({ version: '1.0.0' }, '2.0.0'), false);
  assert.equal(newer(release, '1.2.0'), false);
  assert.throws(() => updateBase('http://example.com'));
  assert.throws(() => validateRelease({ ...release, arch: 'x64' }, base, 'darwin', 'arm64'));
  assert.throws(() => validateRelease({ ...release, download_url: 'https://evil.example/file' }, base, 'darwin', 'arm64'));
});
test('offline forced policy persists; online withdrawal clears it', async t => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'youju-update-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  const cacheFile = path.join(dir, 'cache.json');
  const request = body => async () => ({ ok: true, text: async () => JSON.stringify(body) });
  const first = await checkRelease({ ...options, cacheFile, fetcher: request({ release }) });
  assert.equal(first.release.mandatory, true);
  const down = async () => { throw new Error('offline'); };
  assert.equal((await checkRelease({ ...options, cacheFile, fetcher: down })).release.version, '1.2.0');
  assert.equal((await checkRelease({ ...options, version: '1.2.0', cacheFile, fetcher: down })).release, null);
  assert.equal((await checkRelease({ ...options, cacheFile, fetcher: request({ release: null }) })).release, null);
  assert.equal((await checkRelease({ ...options, cacheFile, fetcher: down })).release, null);
});
test('new offline installs and optional policies do not block', async t => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'youju-update-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  const cacheFile = path.join(dir, 'cache.json'), down = async () => { throw Error('offline'); };
  assert.equal((await checkRelease({ ...options, cacheFile, fetcher: down })).release, null);
  await fs.writeFile(cacheFile, JSON.stringify({ base, release: { ...release, mandatory: false } }));
  assert.equal((await checkRelease({ ...options, cacheFile, fetcher: down })).release, null);
});
test('downloads verify size/hash and remove partial files on error or cancellation', async t => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'youju-update-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  const fetcher = async () => ({ ok: true, body: (async function* () { yield bytes; })() });
  const filename = await downloadMac(release, dir, { fetcher });
  await verifyFile(filename, release);
  await assert.rejects(downloadMac({ ...release, sha512: Buffer.alloc(64).toString('base64') }, dir, { fetcher }), /校验/);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(downloadMac(release, dir, { fetcher, signal: controller.signal }), /取消/);
  assert.equal((await fs.readdir(dir)).some(name => name.endsWith('.part')), false);
});

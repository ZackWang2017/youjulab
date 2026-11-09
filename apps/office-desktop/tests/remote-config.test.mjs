import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_REMOTE, remoteAddress, savedRemoteAddress } from '../remote-config.mjs';

test('migrate bundled MCP default without changing custom servers', () => {
  assert.equal(savedRemoteAddress({ remoteBaseUrl: 'http://127.0.0.1:8000' }), DEFAULT_REMOTE);
  assert.equal(savedRemoteAddress({ remoteBaseUrl: 'https://example.com' }), 'https://example.com');
  assert.equal(savedRemoteAddress({ remoteBaseUrl: 'http://localhost:8000', remoteServiceVersion: 1 }), 'http://localhost:8000');
});
test('normalize addresses and reject credential URLs', () => {
  assert.equal(remoteAddress('192.168.0.5'), DEFAULT_REMOTE);
  assert.equal(remoteAddress('192.168.0.5:9000'), 'http://192.168.0.5:9000');
  assert.throws(() => remoteAddress('file:///tmp/a'));
  assert.throws(() => remoteAddress('https://user:password@example.com'));
});

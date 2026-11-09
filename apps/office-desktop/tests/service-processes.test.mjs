import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { spawn } from 'node:child_process';
import { ServiceProcesses } from '../service-processes.mjs';

function ready(child) {
  return new Promise((resolve, reject) => {
    let text = '';
    const timeout = setTimeout(() => reject(new Error('test process did not start')), 5000);
    child.once('error', reject);
    child.stdout.on('data', chunk => {
      text += chunk;
      if (text.includes('\n')) {
        clearTimeout(timeout);
        resolve(JSON.parse(text.split('\n')[0]));
      }
    });
  });
}

test('terminates a TERM-resistant service and its descendant, leaving unrelated processes alone', { skip: process.platform === 'win32', timeout: 10000 }, async t => {
  const manager = new ServiceProcesses({ graceMs: 80 });
  const unrelated = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { stdio: 'ignore' });
  t.after(() => { unrelated.kill('SIGKILL'); manager.forceStopSync(); });
  const script = `
    const {spawn}=require('node:child_process');
    process.on('SIGTERM',()=>{});
    const child=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});console.log(process.pid);setInterval(()=>{},1000)"],{stdio:['ignore','pipe','ignore']});
    child.stdout.once('data',line=>console.log(JSON.stringify({root:process.pid,descendant:Number(String(line).trim())})));
    setInterval(()=>{},1000);
  `;
  const child = manager.spawn(process.execPath, ['-e', script], { stdio: ['ignore', 'pipe', 'ignore'] });
  const ids = await ready(child);
  const first = manager.stopAll();
  assert.equal(manager.stopAll(), first);
  await first;
  assert.equal(manager.children.size, 0);
  assert.throws(() => process.kill(ids.root, 0), { code: 'ESRCH' });
  assert.throws(() => process.kill(ids.descendant, 0), { code: 'ESRCH' });
  assert.doesNotThrow(() => process.kill(unrelated.pid, 0));
  await manager.stopAll();
});

test('retains a process group when its service root exits first', { skip: process.platform === 'win32', timeout: 10000 }, async t => {
  const manager = new ServiceProcesses({ graceMs: 80 });
  t.after(() => manager.forceStopSync());
  const child = manager.spawn(process.execPath, ['-e', `
    const c=require('node:child_process').spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});console.log(process.pid);setInterval(()=>{},1000)"],{stdio:['ignore','pipe','ignore']});
    c.stdout.once('data',line=>{console.log(JSON.stringify({descendant:Number(String(line).trim())}));process.exit(0)});
  `], { stdio: ['ignore', 'pipe', 'ignore'] });
  const exited = once(child, 'exit');
  const ids = await ready(child);
  await exited;
  assert.equal(manager.children.size, 1);
  await manager.stopAll();
  assert.throws(() => process.kill(ids.descendant, 0), { code: 'ESRCH' });
});

test('Windows uses forced PID-scoped process-tree termination, never name-based killing', async () => {
  const calls = [];
  const manager = new ServiceProcesses({ platform: 'win32', runCommand: async (...args) => calls.push(args) });
  manager.children.set(12345, { pid: 12345 });
  manager.alive = () => false;
  await manager.stopAll();
  assert.equal(calls[0][0], 'taskkill.exe');
  assert.deepEqual(calls[0][1], ['/PID', '12345', '/T', '/F']);
  assert.equal(manager.children.size, 0);
});

test('also terminates a detached descendant process group', { skip: process.platform === 'win32', timeout: 10000 }, async t => {
  const manager = new ServiceProcesses({ graceMs: 80 });
  t.after(() => manager.forceStopSync());
  const child = manager.spawn(process.execPath, ['-e', `
    const c=require('node:child_process').spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});console.log(process.pid);setInterval(()=>{},1000)"],{detached:true,stdio:['ignore','pipe','ignore']});
    c.stdout.once('data',line=>console.log(JSON.stringify({descendant:Number(String(line).trim())})));
    setInterval(()=>{},1000);
  `], { stdio: ['ignore', 'pipe', 'ignore'] });
  const ids = await ready(child);
  await manager.stopAll();
  assert.throws(() => process.kill(ids.descendant, 0), { code: 'ESRCH' });
});

test('shutdown errors preserve tracking so cleanup can be retried', async () => {
  const manager = new ServiceProcesses({ platform: 'win32', runCommand: async () => { throw new Error('denied'); } });
  manager.children.set(12345, { pid: 12345 });
  manager.alive = () => true;
  await assert.rejects(manager.stopAll(), AggregateError);
  assert.equal(manager.children.size, 1);
  assert.equal(manager.stopping, null);
});

test('sync exit fallback force-kills only its owned group', { skip: process.platform === 'win32', timeout: 10000 }, async () => {
  const manager = new ServiceProcesses();
  const child = manager.spawn(process.execPath, ['-e', 'console.log(JSON.stringify({pid:process.pid}));setInterval(()=>{},1000)'], { stdio: ['ignore', 'pipe', 'ignore'] });
  await ready(child);
  const exited = once(child, 'exit');
  manager.forceStopSync();
  await exited;
  await manager.stopAll();
  assert.equal(manager.children.size, 0);
});

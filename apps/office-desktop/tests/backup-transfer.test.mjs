import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileDigest,chooseBackup,downloadBackup,uploadBackup } from '../backup-transfer.mjs';

test('latest uses version, explicit historical selection is preserved',()=>{
  const rows=[{id:'old',version:1,state:'local'},{id:'new',version:2,state:'remote'},
    {id:'legacy',version:100,state:'local',legacy:true},{id:'unfinished',version:101,state:'creating'}];
  assert.equal(chooseBackup(rows).id,'new');assert.equal(chooseBackup(rows,'old').id,'old');
});
test('upload errors preserve local archive; downloads verify content before replacing cache',async t=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'youju-backup-transfer-'));
  t.after(()=>fs.rm(dir,{recursive:true,force:true}));
  const source=path.join(dir,'local.tar.gz'),target=path.join(dir,'remote.tar.gz');
  const bytes=Buffer.from('archive');await fs.writeFile(source,bytes);
  const digest=await fileDigest(source);
  const record={id:'a'.repeat(32),deviceId:'d'.repeat(32),version:1,path:source,
    createdAt:new Date().toISOString(),kind:'manual',name:'youjulab-backup-test.tar.gz',...digest};
  await assert.rejects(uploadBackup('https://admin.example.com','token',record,()=>{},async()=>{throw Error('network down');}));
  assert.deepEqual(await fs.readFile(source),bytes);
  await downloadBackup('https://admin.example.com','token',record,target,()=>{},async()=>({ok:true,body:(async function*(){yield bytes;})()}));
  assert.deepEqual(await fs.readFile(target),bytes);
  await assert.rejects(downloadBackup('https://admin.example.com','token',record,target,()=>{},async()=>({ok:true,body:(async function*(){yield Buffer.from('bad');})()})),/校验/);
  assert.deepEqual(await fs.readFile(target),bytes);
  assert.equal((await fs.readdir(dir)).some(f=>f.endsWith('.part')),false);
});

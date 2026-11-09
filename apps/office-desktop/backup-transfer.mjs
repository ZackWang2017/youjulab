import fs from 'node:fs';
import fsp from 'node:fs/promises';
import { createHash } from 'node:crypto';
import serviceUrls from './service-url.cjs';

export async function fileDigest(filename) {
  const hash=createHash('sha256'); let size=0;
  for await (const chunk of fs.createReadStream(filename)) { hash.update(chunk);size+=chunk.length; }
  return {size,sha256:hash.digest('hex')};
}

function endpoint(base) {
  if (!base) throw new Error('尚未配置在线管理服务地址，本地备份已保留。');
  return serviceUrls.serviceUrl(base);
}

async function checked(response) {
  if (response.ok) return response;
  const body=await response.json().catch(()=>({}));
  throw new Error(body.detail || (response.status===401?'登录已失效，请重新登录。':'服务器暂不可用，请稍后重试。'));
}

export async function remoteBackupList(base,token,deviceId,fetcher=fetch) {
  const response=await checked(await fetcher(`${endpoint(base)}/api/v1/backups?device_id=${encodeURIComponent(deviceId)}`,
    {headers:{Authorization:`Bearer ${token}`},signal:AbortSignal.timeout(15000),redirect:'error'}));
  const body=await response.json();
  if (!Array.isArray(body.records)) throw new Error('服务器备份记录格式异常。');
  return body.records.map(validateRecord);
}

export function validateRecord(raw) {
  if (!raw || !/^[a-f0-9]{32}$/.test(raw.id) || !/^[a-f0-9]{32}$/.test(raw.device_id)
    || !Number.isSafeInteger(raw.client_version) || raw.client_version<1
    || !Number.isSafeInteger(raw.size) || raw.size<1 || raw.size>20*1024**3
    || !/^[a-f0-9]{64}$/.test(raw.sha256)
    || typeof raw.name!=='string' || raw.name.includes('/') || raw.name.includes('\\')
    || !raw.name.startsWith('youjulab-backup-') || !/\.(zip|tar\.gz)$/.test(raw.name)) throw new Error('服务器备份记录校验失败。');
  return raw;
}

export async function uploadBackup(base,token,record,progress=()=>{},fetcher=fetch) {
  const query=new URLSearchParams({device_id:record.deviceId,version:String(record.version),
    created_at:record.createdAt,kind:record.kind,name:record.name});
  let sent=0;
  const stream=fs.createReadStream(record.path);
  const body=(async function*(){for await(const chunk of stream){sent+=chunk.length;progress(Math.min(99,sent/record.size*100));yield chunk;}})();
  try {
    const response=await checked(await fetcher(`${endpoint(base)}/api/v1/backups/${record.id}?${query}`,
      {method:'PUT',body,duplex:'half',headers:{Authorization:`Bearer ${token}`,
        'Content-Type':'application/octet-stream','Content-Length':String(record.size),'X-Content-SHA256':record.sha256},
        signal:AbortSignal.timeout(30*60*1000),redirect:'error'}));
    const remote=validateRecord((await response.json()).record);
    if (remote.id!==record.id || remote.client_version!==record.version || remote.sha256!==record.sha256 || remote.size!==record.size || remote.device_id!==record.deviceId) {
      throw new Error('服务器确认的备份信息不一致，请重试。');
    }
    progress(100);
    return remote;
  } finally { stream.destroy(); }
}

export async function downloadBackup(base,token,record,target,progress=()=>{},fetcher=fetch) {
  const part=target+'.part';
  try {
    const response=await checked(await fetcher(`${endpoint(base)}/api/v1/backups/${record.id}/download?device_id=${record.deviceId}`,
      {headers:{Authorization:`Bearer ${token}`},redirect:'error',signal:AbortSignal.timeout(30*60*1000)}));
    if (!response.body) throw new Error('服务器未返回备份文件。');
    const hash=createHash('sha256');let size=0;
    const file=await fsp.open(part,'w',0o600);
    try {
      for await (const chunk of response.body) {
        size+=chunk.length;
        if(size>record.size) throw new Error('下载文件大小异常，已停止恢复。');
        hash.update(chunk);await file.writeFile(chunk);progress(Math.min(99,size/record.size*100));
      }
    } finally { await file.close(); }
    if (size!==record.size || hash.digest('hex')!==record.sha256) throw new Error('下载校验失败，未恢复任何数据，请重试。');
    await fsp.rename(part,target);progress(100);
    return target;
  } finally { await fsp.rm(part,{force:true}); }
}

export function chooseBackup(records,id='latest') {
  const complete=records.filter(row=>['local','synced','remote','upload_failed'].includes(row.state));
  const modern=complete.filter(row=>!row.legacy);
  const chosen=id==='latest'?(modern.length?modern:complete).sort((a,b)=>b.version-a.version)[0]:complete.find(row=>row.id===id);
  if(!chosen) throw new Error('未找到可以恢复的备份。');
  return chosen;
}

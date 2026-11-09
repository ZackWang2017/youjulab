"""Client backup API. User identities are verified against the configured MCP authority."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tarfile
import uuid
import zipfile

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from backup_repository import BackupRepository


def identifier(value):
    try:
        return uuid.UUID(value).hex
    except (ValueError, AttributeError):
        raise HTTPException(400, '备份或设备编号无效。')


def inspect_archive(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            info=archive.getinfo('manifest.json')
            if info.file_size > 65536:
                raise ValueError('备份说明文件过大。')
            return json.loads(archive.read(info))
    with tarfile.open(path, 'r|gz') as archive:
        scanned=0
        for item in archive:
            scanned += item.size
            if scanned > 64*1024**2:
                break
            if item.name == 'manifest.json' and item.isfile() and item.size <= 65536:
                return json.loads(archive.extractfile(item).read(65537))
    raise ValueError('未找到有效备份说明，请使用新版客户端重新备份。')


def register_routes(app, settings, repo=None, authenticator=None):
    repo = repo or BackupRepository(settings.database_url)

    async def identity(request):
        header=request.headers.get('authorization','')
        if not header.startswith('Bearer ') or len(header)>8192:
            raise HTTPException(401, '请先登录再同步备份。')
        if authenticator:
            return await authenticator(header)
        try:
            async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
                response=await client.get(settings.account_base+'/api/account/me',headers={'Authorization':header})
            if response.status_code in (401,403):
                raise HTTPException(401,'登录已失效，请重新登录。')
            response.raise_for_status()
            body=response.json()
        except (httpx.HTTPError,ValueError):
            raise HTTPException(503,'暂时无法验证账号，本地备份不受影响，请稍后重试。')
        if not body.get('id') or not body.get('email'):
            raise HTTPException(401,'账号信息无效。')
        return {'id':str(body['id']),'email':str(body['email']).lower()}

    def public(row):
        return {key:row[key] for key in ('id','device_id','client_version','created_at','uploaded_at','kind','name','size','sha256')}

    @app.get('/api/v1/backups')
    async def list_backups(request:Request,device_id:str):
        user=await identity(request)
        rows=await run_in_threadpool(repo.list_for,user['id'],identifier(device_id))
        return {'records':[public(row) for row in rows]}

    @app.put('/api/v1/backups/{backup_id}')
    async def upload(request:Request,backup_id:str,device_id:str,version:int,created_at:str,kind:str,name:str):
        user=await identity(request)
        bid,device=identifier(backup_id),identifier(device_id)
        if not 0<version<2**53 or kind not in ('manual','scheduled','pre-restore'):
            raise HTTPException(400,'备份序号或类型无效。')
        if len(name)>240 or '/' in name or '\\' in name or not name.startswith('youjulab-backup-') or not name.endswith(('.zip','.tar.gz')):
            raise HTTPException(400,'备份文件名无效。')
        expected=request.headers.get('x-content-sha256','')
        if not re.fullmatch('[a-f0-9]{64}',expected):
            raise HTTPException(400,'缺少有效文件校验值。')
        try:
            timestamp=datetime.fromisoformat(created_at.replace('Z','+00:00'))
            if timestamp.tzinfo is None:
                raise ValueError()
        except ValueError:
            raise HTTPException(400,'备份时间无效。')
        owner_dir=hashlib.sha256(user['id'].encode()).hexdigest()[:32]
        directory=settings.backup_root/owner_dir/device
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        suffix='.zip' if name.endswith('.zip') else '.tar.gz'
        storage=f'{owner_dir}/{device}/{bid}-{uuid.uuid4().hex}{suffix}'
        final=settings.backup_root/storage
        temporary=final.with_name(final.name+'.upload')
        saved=False
        try:
            digest,size=hashlib.sha256(),0
            with temporary.open('xb') as output:
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>settings.max_backup:
                        raise HTTPException(413,'备份超过服务器允许的大小。')
                    digest.update(chunk)
                    await run_in_threadpool(output.write,chunk)
            if not size or digest.hexdigest()!=expected:
                raise HTTPException(400,'上传校验失败，请重试；本地文件仍保留。')
            try:
                manifest=await run_in_threadpool(inspect_archive,temporary)
            except (ValueError,KeyError,tarfile.TarError,zipfile.BadZipFile):
                raise HTTPException(400,'备份格式无法验证，请重新创建备份。')
            if (manifest.get('scope')!='local-user' or manifest.get('version')!=2
                or manifest.get('account',{}).get('accountId')!=user['id']
                or manifest.get('account',{}).get('email','').lower()!=user['email']
                or manifest.get('backupId')!=bid or manifest.get('deviceId')!=device
                or manifest.get('clientVersion')!=version):
                raise HTTPException(403,'备份与当前登录账号或设备不一致，已拒绝上传。')
            temporary.chmod(0o600)
            temporary.replace(final)
            row={'id':bid,'owner_id':user['id'],'email':user['email'],'device_id':device,
                'client_version':version,'created_at':timestamp,'kind':kind,'name':name,
                'storage_name':storage,'size':size,'sha256':expected}
            try:
                stored=await run_in_threadpool(repo.register,row)
            except ValueError as exc:
                raise HTTPException(409,str(exc))
            if stored['storage_name']!=storage:
                old=settings.backup_root/stored['storage_name']
                if not old.resolve().is_relative_to(settings.backup_root.resolve()):
                    raise HTTPException(500,'服务器备份路径配置异常。')
                if not old.is_file() or old.stat().st_size!=stored['size']:
                    old.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    final.replace(old)
                else:
                    final.unlink(missing_ok=True)
            saved=True
            return {'record':public(stored)}
        finally:
            temporary.unlink(missing_ok=True)
            if not saved:
                final.unlink(missing_ok=True)

    @app.get('/api/v1/backups/{backup_id}/download')
    async def download(request:Request,backup_id:str,device_id:str):
        user=await identity(request)
        row=await run_in_threadpool(repo.get_for,user['id'],identifier(device_id),identifier(backup_id))
        if not row:
            raise HTTPException(404,'未找到当前账号的备份。')
        path=settings.backup_root/row['storage_name']
        if not path.resolve().is_relative_to(settings.backup_root.resolve()) or path.is_symlink() or not path.is_file():
            raise HTTPException(404,'备份文件暂不可用，请联系管理员。')
        return FileResponse(path,media_type='application/octet-stream',filename=row['name'],
                            headers={'X-Content-SHA256':row['sha256'],'Cache-Control':'no-store'})

    return repo

"""Single-process release administration and public desktop update feed."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from pathlib import Path
import re
import secrets
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from config import Settings, from_env
from repository import Repository
from security import LoginLimiter, verify_password
from backups import register_routes as register_backup_routes

HERE = Path(__file__).resolve().parent
log = logging.getLogger('admin-server')
VERSION = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
TARGETS = {('darwin', 'arm64'), ('darwin', 'x64'), ('win32', 'x64')}


def validate_version(value):
    if len(value) > 40 or not VERSION.fullmatch(value):
        raise ValueError('版本号必须为稳定语义版本，如 1.2.3，不允许前导零。')
    return value


def target(platform, arch):
    if (platform, arch) not in TARGETS:
        raise HTTPException(400, '暂不支持该平台或架构。')


class UploadLimit:
    def __init__(self, app, maximum, backup_maximum):
        self.app, self.maximum, self.backup_maximum = app, maximum, backup_maximum

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        count = 0
        maximum = self.maximum if scope.get('path', '').startswith('/admin/upload/') else 64 * 1024
        if scope.get('path','').startswith('/api/v1/backups/'):
            maximum = self.backup_maximum

        async def limited_receive():
            nonlocal count
            message = await receive()
            count += len(message.get('body', b''))
            if count > maximum:
                raise HTTPException(413, '上传请求超过限制。')
            return message

        await self.app(scope, limited_receive, send)


def create_app(settings: Settings | None = None, repository=None, backup_repository=None, backup_authenticator=None):
    settings = (settings or from_env()).validate()
    repo = repository or Repository(settings.database_url)
    app = FastAPI(title='有据 · 在线管理', docs_url=None, redoc_url=None)
    app.state.repo = repo
    limiter = LoginLimiter()
    templates = Jinja2Templates(directory=HERE / 'templates')
    app.add_middleware(UploadLimit, maximum=settings.max_upload + 1024**2,backup_maximum=settings.max_backup)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie='release_admin',
        max_age=8*3600, same_site='strict', https_only=settings.secure_cookie)
    app.mount('/static', StaticFiles(directory=HERE / 'static'), name='static')

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'"
        if request.url.path.startswith(('/admin', '/login', '/api', '/updates')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def admin(request):
        if not request.session.get('admin'):
            raise HTTPException(401, '请先登录管理后台。')

    def csrf(request, form):
        expected = request.session.get('csrf', '')
        if not expected or not hmac.compare_digest(str(form.get('csrf', '')), expected):
            raise HTTPException(403, '页面验证已失效，请刷新后重试。')

    def render(request, name, **context):
        request.session.setdefault('csrf', secrets.token_urlsafe(32))
        return templates.TemplateResponse(request=request, name=name,
            context={'csrf': request.session['csrf'], **context})

    backup_repo=register_backup_routes(app,settings,backup_repository,backup_authenticator)

    @app.get('/admin/backups',response_class=HTMLResponse)
    async def backup_dashboard(request:Request):
        if not request.session.get('admin'):
            return RedirectResponse('/login',status_code=303)
        rows=await run_in_threadpool(backup_repo.admin_list)
        return render(request,'backups.html',records=rows,backup_root=str(settings.backup_root))

    @app.get('/', include_in_schema=False)
    async def home(request: Request):
        response = RedirectResponse('/admin' if request.session.get('admin') else '/login', status_code=303)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/health')
    async def health():
        return {'ok': True}

    @app.get('/login', response_class=HTMLResponse)
    async def login_page(request: Request):
        return render(request, 'login.html')

    @app.post('/login')
    async def login(request: Request):
        key = request.client.host if request.client else 'unknown'
        if not limiter.allowed(key):
            raise HTTPException(429, '登录尝试过多，请十分钟后重试。')
        form = await request.form()
        csrf(request, form)
        matched = await run_in_threadpool(verify_password, str(form.get('password', '')), settings.password_hash)
        if not hmac.compare_digest(str(form.get('username', '')), settings.admin_username) or not matched:
            limiter.failed(key)
            return render(request, 'login.html', error='账号或密码不正确。')
        request.session.clear()
        request.session.update(admin=True, csrf=secrets.token_urlsafe(32))
        limiter.failures.pop(key, None)
        return RedirectResponse('/admin', status_code=303)

    @app.get('/admin', response_class=HTMLResponse)
    async def dashboard(request: Request):
        if not request.session.get('admin'):
            return RedirectResponse('/login', status_code=303)
        rows = await run_in_threadpool(repo.all)
        return render(request, 'admin.html', releases=rows, error=request.query_params.get('error', ''),
                      upload_mb=settings.max_upload // 1024**2)

    @app.post('/admin/logout')
    async def logout(request: Request):
        admin(request)
        csrf(request, await request.form())
        request.session.clear()
        return RedirectResponse('/login', status_code=303)

    @app.post('/admin/releases')
    async def create(request: Request):
        admin(request)
        form = await request.form()
        csrf(request, form)
        try:
            await run_in_threadpool(repo.create, validate_version(str(form.get('version', '')).strip()),
                str(form.get('notes', ''))[:20000], form.get('mandatory') == 'on')
        except ValueError as exc:
            return admin_error(str(exc))
        return RedirectResponse('/admin', status_code=303)

    def admin_error(message):
        from urllib.parse import urlencode
        return RedirectResponse('/admin?' + urlencode({'error': message}), status_code=303)

    @app.post('/admin/releases/{identifier}/{action}')
    async def mutation(request: Request, identifier: str, action: str):
        admin(request)
        form = await request.form()
        csrf(request, form)
        try:
            if action == 'edit':
                await run_in_threadpool(repo.edit, identifier, str(form.get('notes', ''))[:20000], form.get('mandatory') == 'on')
            elif action == 'publish':
                await run_in_threadpool(repo.publish, identifier)
            elif action == 'latest':
                await run_in_threadpool(repo.set_latest, identifier)
            elif action == 'withdraw':
                await run_in_threadpool(repo.withdraw, identifier, str(form.get('replacement', '')))
            elif action == 'delete':
                files = await run_in_threadpool(repo.delete_draft, identifier)
                for name in files:
                    (settings.files / name).unlink(missing_ok=True)
            else:
                raise HTTPException(404)
        except ValueError as exc:
            return admin_error(str(exc))
        return RedirectResponse('/admin', status_code=303)

    @app.post('/admin/pause')
    async def pause(request: Request):
        admin(request)
        csrf(request, await request.form())
        await run_in_threadpool(repo.set_latest, None)
        return RedirectResponse('/admin', status_code=303)

    @app.post('/admin/upload/{identifier}')
    async def upload(request: Request, identifier: str):
        admin(request)
        temporary = final = None
        try:
            async with request.form(max_files=1, max_fields=10, max_part_size=1024**2) as form:
                csrf(request, form)
                platform, arch = str(form.get('platform', '')), str(form.get('arch', ''))
                target(platform, arch)
                file = form.get('package')
                suffix = '.dmg' if platform == 'darwin' else '.exe'
                if not file or not hasattr(file, 'read') or not (file.filename or '').lower().endswith(suffix):
                    raise ValueError('请上传对应平台的 DMG / NSIS EXE 安装包。')
                settings.files.mkdir(parents=True, exist_ok=True, mode=0o700)
                name = uuid.uuid4().hex + suffix
                final, temporary = settings.files / name, settings.files / (name + '.upload')
                size, digest = 0, hashlib.sha512()
                with temporary.open('xb') as output:
                    while chunk := await file.read(1024**2):
                        size += len(chunk)
                        if size > settings.max_upload:
                            raise HTTPException(413, '安装包超过上传大小限制。')
                        digest.update(chunk)
                        await run_in_threadpool(output.write, chunk)
                if not size:
                    raise ValueError('安装包不能为空。')
                temporary.replace(final)
                old = await run_in_threadpool(repo.add_artifact, identifier, platform, arch, name, size,
                                             base64.b64encode(digest.digest()).decode())
                final = None  # Database now owns the immutable file.
                if old:
                    (settings.files / old).unlink(missing_ok=True)
        except ValueError as exc:
            return admin_error(str(exc))
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
            if final:
                final.unlink(missing_ok=True)
        return RedirectResponse('/admin', status_code=303)

    def public(row):
        return {'id': row['id'], 'version': row['version'], 'notes': row['notes'], 'mandatory': row['mandatory'],
            'platform': row['platform'], 'arch': row['arch'], 'size': row['size'], 'sha512': row['sha512'],
            'download_url': f"{settings.public_url}/packages/{row['artifact_id']}/{row['filename']}",
            'update_feed_url': f"{settings.public_url}/updates/{row['platform']}/{row['arch']}/{row['id']}"}

    @app.get('/release', response_class=HTMLResponse)
    async def release_downloads(request: Request):
        rows = await run_in_threadpool(repo.all)
        releases = [row for row in rows if row['status'] == 'published']
        releases.sort(key=lambda row: tuple(int(part) for part in row['version'].split('.')), reverse=True)
        response = templates.TemplateResponse(request=request, name='release.html', context={'releases': releases})
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/v1/releases/latest')
    async def latest(platform: str, arch: str):
        target(platform, arch)
        row = await run_in_threadpool(repo.latest, platform, arch)
        return {'release': public(row) if row else None}

    @app.get('/api/v1/releases/history')
    async def history(platform: str, arch: str):
        target(platform, arch)
        rows = await run_in_threadpool(repo.all)
        return {'releases': [{'version': r['version'], 'notes': r['notes'], 'published_at': r['published_at'],
                              'latest': r['latest']} for r in rows if r['status'] == 'published'
                            and any(a['platform'] == platform and a['arch'] == arch for a in r['artifacts'])]}

    @app.get('/packages/{identifier}/{filename}')
    async def download(identifier: str, filename: str):
        row = await run_in_threadpool(repo.artifact, identifier)
        if not row or filename != row['filename']:
            raise HTTPException(404)
        path = settings.files / row['filename']
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, '安装包文件不存在。')
        return FileResponse(path, filename=f"youjulab-{row['version']}{path.suffix}", media_type='application/octet-stream')

    @app.get('/updates/win32/x64/{identifier}/latest.yml')
    async def windows_feed(identifier: str):
        rows = await run_in_threadpool(repo.all)
        release = next((r for r in rows if r['id'] == identifier and r['status'] == 'published'), None)
        asset = next((a for a in release['artifacts'] if a['platform'] == 'win32' and a['arch'] == 'x64'), None) if release else None
        if not asset:
            raise HTTPException(404)
        url = f"{settings.public_url}/packages/{asset['id']}/{asset['filename']}"
        # JSON strings are valid YAML scalars; never interpolate free-form notes as YAML.
        content = f"version: {json.dumps(release['version'])}\nfiles:\n  - url: {json.dumps(url)}\n    sha512: {json.dumps(asset['sha512'])}\n    size: {asset['size']}\npath: {json.dumps(url)}\nsha512: {json.dumps(asset['sha512'])}\n"
        return Response(content, media_type='application/yaml')

    @app.exception_handler(Exception)
    async def unexpected(request, error):
        log.error('Release request failed: %s', type(error).__name__)
        return JSONResponse({'detail': '版本服务暂时不可用，请检查服务器日志或稍后重试。'}, status_code=503)

    return app


if __name__ == '__main__':
    import os
    from dotenv import load_dotenv
    import uvicorn
    load_dotenv(HERE / '.env')
    uvicorn.run(create_app(), host=os.environ.get('ADMIN_HOST') or os.environ.get('RELEASE_HOST', '127.0.0.1'), port=int(os.environ.get('ADMIN_PORT') or os.environ.get('RELEASE_PORT', '8200')))

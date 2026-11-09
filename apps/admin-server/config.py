from dataclasses import dataclass
import os
from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class Settings:
    database_url: str
    files: Path
    session_secret: str
    password_hash: str
    admin_username: str = 'admin'
    public_url: str = ''
    secure_cookie: bool = True
    max_upload: int = 2 * 1024**3
    backup_root: Path = Path('/Users/zackwang/WorkSpace/BaiduSynSpace/youjulab')
    account_base: str = 'http://192.168.0.5:8000'
    max_backup: int = 20 * 1024**3

    def validate(self):
        parsed = urlparse(self.database_url)
        if parsed.scheme not in ('postgres', 'postgresql') or unquote(parsed.path.strip('/')) not in ('admin-server', 'release-server'):
            raise ValueError('ADMIN_DATABASE_URL 必须指向独立管理库 admin-server（兼容原 release-server）。')
        if len(self.session_secret) < 32:
            raise ValueError('RELEASE_SESSION_SECRET 至少 32 个字符。')
        if not self.password_hash.startswith('pbkdf2_sha256$'):
            raise ValueError('请先生成 RELEASE_ADMIN_PASSWORD_HASH。')
        public = urlparse(self.public_url)
        if not public.hostname or public.username or public.password or public.query or public.fragment:
            raise ValueError('请设置有效的 RELEASE_PUBLIC_URL。')
        local_http = public.hostname in ('localhost', '127.0.0.1', '::1')
        try:
            address = ip_address(public.hostname)
            local_http = local_http or any(address in ip_network(network) for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
        except ValueError:
            pass
        if public.scheme != 'https' and (public.scheme != 'http' or self.secure_cookie or not local_http):
            raise ValueError('正式发布要求 HTTPS；仅本机开发可关闭安全 Cookie 使用 HTTP。')
        if self.max_upload <= 0:
            raise ValueError('上传限制必须大于零。')
        authority=urlparse(self.account_base)
        if authority.scheme not in ('http','https') or not authority.hostname or authority.username or authority.password or authority.query or authority.fragment:
            raise ValueError('ADMIN_ACCOUNT_BASE_URL 必须是有效的 MCP 认证服务地址。')
        if self.max_backup <= 0:
            raise ValueError('备份上传限制必须大于零。')
        return self


def from_env():
    return Settings(
        database_url=os.environ.get('ADMIN_DATABASE_URL') or os.environ.get('RELEASE_DATABASE_URL', ''),
        files=Path(os.environ.get('RELEASE_FILE_ROOT', './release-data/packages')).expanduser().resolve(),
        session_secret=os.environ.get('ADMIN_SESSION_SECRET') or os.environ.get('RELEASE_SESSION_SECRET', ''),
        password_hash=os.environ.get('ADMIN_PASSWORD_HASH') or os.environ.get('RELEASE_ADMIN_PASSWORD_HASH', ''),
        public_url=(os.environ.get('ADMIN_PUBLIC_URL') or os.environ.get('RELEASE_PUBLIC_URL', '')).rstrip('/'),
        secure_cookie=os.environ.get('RELEASE_COOKIE_SECURE', '1') != '0',
        max_upload=int(os.environ.get('RELEASE_MAX_UPLOAD_BYTES', str(2 * 1024**3))),
        backup_root=Path(os.environ.get('ADMIN_BACKUP_ROOT', '/Users/zackwang/WorkSpace/BaiduSynSpace/youjulab')).expanduser().resolve(),
        account_base=os.environ.get('ADMIN_ACCOUNT_BASE_URL', 'http://192.168.0.5:8000').rstrip('/'),
        max_backup=int(os.environ.get('ADMIN_MAX_BACKUP_BYTES', str(20 * 1024**3))),
    ).validate()

"""Local privacy-screen preferences; deliberately not an OS/account security boundary."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from threading import RLock

from fastapi import HTTPException
from config import sqlite_path

BACKGROUNDS = ('orbit', 'stars', 'lake')
_mutex = RLock()
_attempts: dict[str, tuple[int, float]] = {}


def _path(owner: str) -> Path:
    return sqlite_path().parent / 'screen-lock' / (hashlib.sha256(owner.encode()).hexdigest() + '.json')


def _read(owner: str) -> dict:
    path = _path(owner)
    if not path.exists():
        return {'background': 'stars'}
    # A corrupt preferences file must not silently remove password protection.
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('屏保配置损坏，请检查本机 screen-lock 配置文件。')
    return data


def _public(data: dict) -> dict:
    return {'background': data.get('background', 'stars'), 'password_enabled': bool(data.get('digest'))}


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 210_000).hex()


def _verify(owner: str, data: dict, password: str):
    if not data.get('digest'):
        return
    now = time.monotonic()
    count, until = _attempts.get(owner, (0, 0))
    if until > now:
        raise HTTPException(429, '尝试次数较多，请在 30 秒后重试。')
    if count >= 5:
        count = 0
    if not hmac.compare_digest(_hash(password, data['salt']), data['digest']):
        count += 1
        _attempts[owner] = (count, now + 30 if count >= 5 else 0)
        raise HTTPException(403, '屏保密码不正确。')
    _attempts.pop(owner, None)


def preferences(owner: str) -> dict:
    with _mutex:
        return _public(_read(owner))


def update_preferences(owner: str, *, background: str, password_enabled: bool,
                       current_password: str = '', new_password: str = '') -> dict:
    if background not in BACKGROUNDS:
        raise HTTPException(400, '请选择有效的屏保背景。')
    if len(current_password) > 128 or len(new_password) > 128:
        raise HTTPException(400, '屏保密码不能超过 128 位。')
    with _mutex:
        data = _read(owner)
        changing = bool(new_password) or (bool(data.get('digest')) != password_enabled)
        if changing:
            _verify(owner, data, current_password)
            if password_enabled:
                if len(new_password) < 4:
                    raise HTTPException(400, '请设置至少 4 位的屏保密码。')
                salt = secrets.token_hex(16)
                data.update(salt=salt, digest=_hash(new_password, salt))
            else:
                data.pop('salt', None)
                data.pop('digest', None)
        data['background'] = background
        path = _path(owner)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.screen-lock-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return _public(data)


def unlock(owner: str, password: str) -> dict:
    if len(password) > 128:
        raise HTTPException(400, '屏保密码不能超过 128 位。')
    with _mutex:
        _verify(owner, _read(owner), password)
        return {'ok': True}

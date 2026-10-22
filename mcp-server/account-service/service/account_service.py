"""注册、登录、改密。密码哈希与本地办公库同一套 PBKDF2，但账号表独立。"""
from __future__ import annotations

import hashlib
import re
import secrets
from uuid import uuid4

import account_repository as repo
import captcha_service
from account_jwt import issue

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.status = status
        super().__init__(message)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return secrets.compare_digest(check, digest)


def public_account(row: dict) -> dict:
    return {"id": row["id"], "email": row["email"]}


def _check_captcha(body: dict) -> None:
    if not captcha_service.check(str(body.get("captcha_id") or ""), str(body.get("captcha_code") or "")):
        raise AccountError("验证码不正确或已过期。", 400)


def register(body: dict) -> dict:
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if not _EMAIL.match(email):
        raise AccountError("邮箱格式不正确。")
    if len(password) < 6:
        raise AccountError("密码至少 6 位。")
    _check_captcha(body)
    repo.ensure_schema()
    if repo.get_by_email(email):
        raise AccountError("该邮箱已注册。", 409)
    row = repo.insert(uuid4().hex, email, hash_password(password))
    token = issue(row["id"], row["email"])
    return {"token": token, "user": public_account(row)}


def login(body: dict) -> dict:
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    _check_captcha(body)
    repo.ensure_schema()
    row = repo.get_by_email(email)
    if not row or not verify_password(password, row["password_hash"]):
        raise AccountError("邮箱或密码不正确。", 401)
    token = issue(row["id"], row["email"])
    return {"token": token, "user": public_account(row)}


def change_password(account_id: str, current_password: str, new_password: str) -> None:
    if len(new_password) < 6:
        raise AccountError("新密码至少 6 位。")
    repo.ensure_schema()
    row = repo.get_by_id(account_id)
    if not row or not verify_password(current_password, row["password_hash"]):
        raise AccountError("当前密码不正确。", 400)
    repo.update_password(account_id, hash_password(new_password))


def issue_agent_token(account_id: str) -> dict:
    repo.ensure_schema()
    row = repo.get_by_id(account_id)
    if not row:
        raise AccountError("请先登录。", 401)
    token = issue(row["id"], row["email"], days=90, kind="agent")
    return {"access_token": token, "expires_in_days": 90}


def me(account_id: str) -> dict:
    repo.ensure_schema()
    row = repo.get_by_id(account_id)
    if not row:
        raise AccountError("请先登录。", 401)
    return public_account(row)

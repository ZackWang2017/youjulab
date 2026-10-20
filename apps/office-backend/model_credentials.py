"""用户自有模型凭据的加密存储与对外脱敏表示。"""
from __future__ import annotations

import base64
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config import user_model_encryption_key

_AAD_PREFIX = b"office-model-credentials:v1:"


class CredentialEncryptionError(ValueError):
    pass


def _key() -> bytes:
    key = user_model_encryption_key()
    if not key:
        raise CredentialEncryptionError(
            "服务未配置有效的 OFFICE_MODEL_CREDENTIALS_KEY，不能安全保存自有模型密钥。"
        )
    return key


def _aad(user_id: str) -> bytes:
    return _AAD_PREFIX + user_id.encode("utf-8")


def encrypt_api_key(user_id: str, api_key: str) -> tuple[str, str]:
    nonce = os.urandom(12)
    encrypted = AESGCM(_key()).encrypt(nonce, api_key.encode("utf-8"), _aad(user_id))
    return (
        base64.urlsafe_b64encode(encrypted).decode("ascii"),
        base64.urlsafe_b64encode(nonce).decode("ascii"),
    )


def decrypt_api_key(user_id: str, record: dict[str, Any]) -> str:
    try:
        ciphertext = base64.urlsafe_b64decode(str(record["api_key_ciphertext"]).encode("ascii"))
        nonce = base64.urlsafe_b64decode(str(record["api_key_nonce"]).encode("ascii"))
        return AESGCM(_key()).decrypt(nonce, ciphertext, _aad(user_id)).decode("utf-8")
    except CredentialEncryptionError:
        raise
    except Exception as exc:
        raise CredentialEncryptionError("自有模型密钥无法解密，请在设置中重新保存。") from exc


def public_model_config(record: dict[str, Any] | None) -> dict[str, Any]:
    if not record:
        return {
            "configured": False,
            "provider": "deepseek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
            "key_last4": "",
        }
    return {
        "configured": True,
        "provider": record.get("provider") or "deepseek",
        "model": record.get("model") or "deepseek-flash",
        "base_url": record.get("base_url") or "https://api.deepseek.com",
        "key_last4": record.get("api_key_last4") or "",
        "updated_at": record.get("updated_at"),
    }

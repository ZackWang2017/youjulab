"""表格正文口令加密：scrypt 派生密钥 + AES-256-GCM。密码不落库。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT = "youju-sheet-encrypted-v1"
KDF = "scrypt"
KDF_PARAMS = {"n": 32768, "r": 8, "p": 1, "dklen": 32}
MIN_PASSWORD = 6
LOCKED_MESSAGE = "表格已加密，请先在编辑器中解锁。"
WRONG_PASSWORD = "文件密码不正确。"


class SheetCryptoError(ValueError):
    pass


def is_encrypted(content: Any) -> bool:
    return isinstance(content, dict) and content.get("format") == FORMAT


def encrypt_sheet(plaintext: Any, password: str, *, asset_id: str = "", kdf_params: dict | None = None) -> dict:
    secret = _password(password)
    params = {**KDF_PARAMS, **(kdf_params or {})}
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive(secret, salt, params)
    payload = json.dumps(plaintext, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, payload, _aad(asset_id))
    return {
        "format": FORMAT,
        "kdf": KDF,
        "kdf_params": params,
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
    }


def decrypt_sheet(envelope: Any, password: str, *, asset_id: str = "") -> Any:
    if not is_encrypted(envelope):
        raise SheetCryptoError("表格未加密。")
    secret = _password(password)
    try:
        salt = _unb64(envelope.get("salt"))
        nonce = _unb64(envelope.get("nonce"))
        ciphertext = _unb64(envelope.get("ciphertext"))
        params = envelope.get("kdf_params") if isinstance(envelope.get("kdf_params"), dict) else KDF_PARAMS
        if envelope.get("kdf") not in (None, KDF):
            raise SheetCryptoError("不支持的密钥派生方式。")
        key = _derive(secret, salt, params)
        raw = AESGCM(key).decrypt(nonce, ciphertext, _aad(asset_id))
        return json.loads(raw.decode("utf-8"))
    except SheetCryptoError:
        raise
    except InvalidTag:
        raise SheetCryptoError(WRONG_PASSWORD) from None
    except Exception as exc:
        raise SheetCryptoError("密文无法解密。") from exc


def _password(password: str) -> bytes:
    text = str(password or "")
    if len(text) < MIN_PASSWORD:
        raise SheetCryptoError(f"文件密码至少 {MIN_PASSWORD} 位。")
    return text.encode("utf-8")


def _derive(password: bytes, salt: bytes, params: dict) -> bytes:
    n = int(params.get("n") or KDF_PARAMS["n"])
    r = int(params.get("r") or KDF_PARAMS["r"])
    p = int(params.get("p") or KDF_PARAMS["p"])
    dklen = int(params.get("dklen") or KDF_PARAMS["dklen"])
    return hashlib.scrypt(password, salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=64 * 1024 * 1024)


def _aad(asset_id: str) -> bytes:
    return f"{FORMAT}:{asset_id}".encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _unb64(value: Any) -> bytes:
    return base64.urlsafe_b64decode(str(value or "").encode("ascii"))

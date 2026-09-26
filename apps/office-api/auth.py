"""JWT 签发与校验。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import jwt_secret
from store import Store, public_user

bearer = HTTPBearer(auto_error=False)


def create_token(user_id: str) -> str:
    payload = {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(days=7)}
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def read_token(token: str) -> str:
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "登录已失效，请重新登录。") from exc
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(401, "登录已失效，请重新登录。")
    return user_id


def current_user(store: Store, creds: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(401, "请先登录。")
    user = store.user_by_id(read_token(creds.credentials))
    if not user:
        raise HTTPException(401, "请先登录。")
    return public_user(user)

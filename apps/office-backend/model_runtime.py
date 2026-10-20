"""按请求选择平台或当前用户模型，不把用户密钥暴露到 HTTP 响应。"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from config import llm_config_set, model_api_key as platform_api_key, model_base_url as platform_base_url, model_name as platform_name
from model_credentials import CredentialEncryptionError, decrypt_api_key
from store import Store


@dataclass(frozen=True)
class ModelSettings:
    source: str
    base_url: str = ""
    name: str = ""
    api_key: str = ""
    error: str = ""


_current: ContextVar[ModelSettings | None] = ContextVar("office_model_settings", default=None)


def platform_settings() -> ModelSettings:
    return ModelSettings("platform", platform_base_url(), platform_name(), platform_api_key())


def user_settings(store: Store, user_id: str) -> ModelSettings:
    record = store.get_user_model_config(user_id)
    if not record:
        return ModelSettings("user", error="请先在设置中保存自有 DeepSeek API Key。")
    try:
        key = decrypt_api_key(user_id, record)
    except CredentialEncryptionError as exc:
        return ModelSettings("user", error=str(exc))
    return ModelSettings(
        "user",
        str(record.get("base_url") or "").rstrip("/"),
        str(record.get("model") or "").strip(),
        key.strip(),
    )


def settings_for_user(store: Store, user_id: str) -> ModelSettings:
    mode = llm_config_set()
    if mode == 0:
        return platform_settings()
    if mode in (1, 3):
        return user_settings(store, user_id)
    return ModelSettings("invalid", error="LLM_CONFIG_SET 仅支持 0（平台）、1（自有）或 3（按用户选择）。")


def set_current_model(settings: ModelSettings) -> Token:
    return _current.set(settings)


def reset_current_model(token: Token) -> None:
    _current.reset(token)


def current_settings() -> ModelSettings:
    return _current.get() or platform_settings()


def model_base_url() -> str:
    return current_settings().base_url


def model_name() -> str:
    return current_settings().name


def model_api_key() -> str:
    return current_settings().api_key


def model_configured() -> bool:
    settings = current_settings()
    return bool(settings.base_url and settings.name and (settings.source == "platform" or settings.api_key))


def model_configuration_error() -> str:
    settings = current_settings()
    if settings.error:
        return settings.error
    if settings.source == "user" and not settings.api_key:
        return "请先在设置中保存自有 DeepSeek API Key。"
    return "请先配置模型的 API 地址和模型名称。"

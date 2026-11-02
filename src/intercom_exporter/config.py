import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-4.5"
DEFAULT_OPENROUTER_WORKERS = 3


@dataclass(frozen=True)
class OpenRouterSettings:
    api_key: str
    model: str
    workers: int = DEFAULT_OPENROUTER_WORKERS


def load_openrouter_settings(env_file: str | Path | None = None) -> OpenRouterSettings | None:
    load_dotenv(dotenv_path=env_file, override=False)
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.getenv("OPENROUTER_MODEL", "").strip() or DEFAULT_OPENROUTER_MODEL
    try:
        workers = int(os.getenv("OPENROUTER_WORKERS", str(DEFAULT_OPENROUTER_WORKERS)))
    except ValueError as exc:
        raise ValueError("OPENROUTER_WORKERS должен быть целым числом от 1 до 10.") from exc
    if not 1 <= workers <= 10:
        raise ValueError("OPENROUTER_WORKERS должен быть целым числом от 1 до 10.")
    return OpenRouterSettings(api_key, model, workers)


@dataclass(frozen=True)
class Config:
    access_token: str
    api_version: str
    openrouter_api_key: str | None = None
    openrouter_model: str = DEFAULT_OPENROUTER_MODEL
    openrouter_workers: int = DEFAULT_OPENROUTER_WORKERS


def load_config(env_file: str | Path | None = None) -> Config:
    load_dotenv(dotenv_path=env_file, override=False)
    token = os.getenv("INTERCOM_ACCESS_TOKEN", "").strip()
    version = os.getenv("INTERCOM_API_VERSION", "2.17").strip()
    if not token:
        raise ValueError("Не задан INTERCOM_ACCESS_TOKEN. Скопируйте .env.example в .env и заполните токен Intercom.")
    if not version:
        raise ValueError("Не задан INTERCOM_API_VERSION. Укажите версию API в файле .env.")
    openrouter = load_openrouter_settings(env_file)
    return Config(
        token,
        version,
        openrouter.api_key if openrouter else None,
        openrouter.model if openrouter else DEFAULT_OPENROUTER_MODEL,
        openrouter.workers if openrouter else DEFAULT_OPENROUTER_WORKERS,
    )

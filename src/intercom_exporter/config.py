import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-4.5"
DEFAULT_OPENROUTER_WORKERS = 3

DEFAULT_DAHL_MODEL = "MiniMaxAI/MiniMax-M2.7"
DEFAULT_DAHL_WORKERS = 3


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
class DahlSettings:
    api_key: str
    model: str
    workers: int = DEFAULT_DAHL_WORKERS


def load_dahl_settings(env_file: str | Path | None = None) -> DahlSettings | None:
    load_dotenv(dotenv_path=env_file, override=False)
    api_key = os.getenv("DAHL_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.getenv("DAHL_MODEL", "").strip() or DEFAULT_DAHL_MODEL
    try:
        workers = int(os.getenv("DAHL_WORKERS", str(DEFAULT_DAHL_WORKERS)))
    except ValueError as exc:
        raise ValueError("DAHL_WORKERS должен быть целым числом от 1 до 10.") from exc
    if not 1 <= workers <= 10:
        raise ValueError("DAHL_WORKERS должен быть целым числом от 1 до 10.")
    return DahlSettings(api_key, model, workers)


DEFAULT_AGENT_WORKERS = 10


def _read_env_value(path: Path, key: str) -> str:
    """Простой парсер .env без побочного эффекта load_dotenv (загрязнение os.environ)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def load_agent_workers(env_file: str | Path | None = None) -> int:
    """Число субагентов оркестрации по умолчанию: .env AGENT_WORKERS (2–20), иначе 10.

    Без load_dotenv: переменная читается из os.environ или парсится из файла напрямую,
    чтобы не влиять на другие настройки и тесты.
    """
    if env_file is not None:
        raw = _read_env_value(Path(env_file), "AGENT_WORKERS")
    else:
        raw = os.getenv("AGENT_WORKERS", "").strip()
        if not raw:
            raw = _read_env_value(Path(".env"), "AGENT_WORKERS")
    if not raw:
        return DEFAULT_AGENT_WORKERS
    try:
        workers = int(raw)
    except ValueError as exc:
        raise ValueError("AGENT_WORKERS должен быть целым числом от 2 до 20.") from exc
    if not 2 <= workers <= 20:
        raise ValueError("AGENT_WORKERS должен быть целым числом от 2 до 20.")
    return workers


@dataclass(frozen=True)
class Config:
    access_token: str
    api_version: str
    openrouter_api_key: str | None = None
    openrouter_model: str = DEFAULT_OPENROUTER_MODEL
    openrouter_workers: int = DEFAULT_OPENROUTER_WORKERS
    dahl_api_key: str | None = None
    dahl_model: str = DEFAULT_DAHL_MODEL
    dahl_workers: int = DEFAULT_DAHL_WORKERS


def load_config(env_file: str | Path | None = None) -> Config:
    load_dotenv(dotenv_path=env_file, override=False)
    token = os.getenv("INTERCOM_ACCESS_TOKEN", "").strip()
    version = os.getenv("INTERCOM_API_VERSION", "2.17").strip()
    if not token:
        raise ValueError("Не задан INTERCOM_ACCESS_TOKEN. Скопируйте .env.example в .env и заполните токен Intercom.")
    if not version:
        raise ValueError("Не задан INTERCOM_API_VERSION. Укажите версию API в файле .env.")
    openrouter = load_openrouter_settings(env_file)
    dahl = load_dahl_settings(env_file)
    return Config(
        token,
        version,
        openrouter.api_key if openrouter else None,
        openrouter.model if openrouter else DEFAULT_OPENROUTER_MODEL,
        openrouter.workers if openrouter else DEFAULT_OPENROUTER_WORKERS,
        dahl.api_key if dahl else None,
        dahl.model if dahl else DEFAULT_DAHL_MODEL,
        dahl.workers if dahl else DEFAULT_DAHL_WORKERS,
    )

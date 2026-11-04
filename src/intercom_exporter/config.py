import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


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
class SlackSettings:
    bot_token: str
    channel_id: str = ""


def load_slack_settings(env_file: str | Path | None = None) -> SlackSettings | None:
    """Настройки Slack: .env SLACK_BOT_TOKEN (+ SLACK_CHANNEL_ID).

    Без load_dotenv, как load_agent_workers: переменные читаются из os.environ
    или парсятся из файла напрямую, чтобы не загрязнять os.environ для тестов.
    """
    if env_file is not None:
        token = _read_env_value(Path(env_file), "SLACK_BOT_TOKEN")
        channel = _read_env_value(Path(env_file), "SLACK_CHANNEL_ID")
    else:
        token = os.getenv("SLACK_BOT_TOKEN", "").strip() or _read_env_value(Path(".env"), "SLACK_BOT_TOKEN")
        channel = os.getenv("SLACK_CHANNEL_ID", "").strip() or _read_env_value(Path(".env"), "SLACK_CHANNEL_ID")
    if not token:
        return None
    return SlackSettings(token, channel)


@dataclass(frozen=True)
class Config:
    access_token: str
    api_version: str


def load_config(env_file: str | Path | None = None) -> Config:
    load_dotenv(dotenv_path=env_file, override=False)
    token = os.getenv("INTERCOM_ACCESS_TOKEN", "").strip()
    version = os.getenv("INTERCOM_API_VERSION", "2.17").strip()
    if not token:
        raise ValueError("Не задан INTERCOM_ACCESS_TOKEN. Скопируйте .env.example в .env и заполните токен Intercom.")
    if not version:
        raise ValueError("Не задан INTERCOM_API_VERSION. Укажите версию API в файле .env.")
    return Config(token, version)

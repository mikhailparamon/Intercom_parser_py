import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


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

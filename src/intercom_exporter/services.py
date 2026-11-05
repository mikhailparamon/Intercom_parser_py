"""Реестр сервисов, которые проект может обрабатывать."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Service:
    id: str
    label: str
    token_env: str
    folder_name: str


SERVICES: dict[str, Service] = {
    "vpn": Service(
        id="vpn",
        label="VPNНаоборот",
        token_env="INTERCOM_ACCESS_TOKEN",
        folder_name="VPNnaoborot",
    ),
    "hypertube": Service(
        id="hypertube",
        label="HyperTube",
        token_env="HYPERTUBE_INTERCOM_ACCESS_TOKEN",
        folder_name="Hypertube",
    ),
}


DEFAULT_SERVICE_ID = "vpn"


def get_service(service_id: str | None = None) -> Service:
    """Возвращает сервис по ID; при None — сервис по умолчанию."""
    key = service_id or DEFAULT_SERVICE_ID
    if key not in SERVICES:
        raise ValueError(f"Неизвестный сервис: {key}. Доступные: {', '.join(SERVICES)}")
    return SERVICES[key]


def iter_services():
    """Порядок обхода сервисов при полном цикле."""
    return iter(SERVICES.values())

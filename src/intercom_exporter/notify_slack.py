"""Отправка dashboard.html и структурированной сводки в Slack-канал.

Использует Bot Token (xoxb-...) и новый flow загрузки файлов:
files.getUploadURLExternal → POST содержимого → files.completeUploadExternal.

Текст сообщения строится из dashboard_DD_MM_YYYY.json, который write_dashboard
пишет рядом с HTML.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import requests

SLACK_API = "https://slack.com/api"
DEFAULT_TIMEOUT = 30


class SlackNotifyError(RuntimeError):
    pass


def _plural_conversations(count: int) -> str:
    """Склонение 'обращение': 1 обращение, 3 обращения, 5 обращений."""
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} обращение"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} обращения"
    return f"{count} обращений"


def _dd_mm(iso_date: str) -> str:
    """'2026-11-03' → '03.11'."""
    return date.fromisoformat(iso_date).strftime("%d.%m")


def _new_today_text(daily: list[dict]) -> str:
    """Сравнение новых обращений: последний полный день против предыдущего.

    Последний элемент daily — текущий неполный день, поэтому сравниваются
    daily[-2] и daily[-3]. Если дней меньше трёх, текст не выводится;
    если на предыдущий день пришлось 0 обращений, процент — 'н/д'.
    """
    if len(daily) < 3:
        return ""
    current, previous = daily[-2], daily[-3]
    text = f"(Новых за {_dd_mm(current['date'])}: {current['count']} шт., "
    if not previous["count"]:
        return text + f"н/д к {_dd_mm(previous['date'])})"
    percent = (current["count"] - previous["count"]) / previous["count"] * 100
    return text + f"{percent:+.1f}% к {_dd_mm(previous['date'])})"


def _top_categories_text(title: str, categories: list[dict]) -> str:
    """Строка вида 'Топ-3 категорий за сутки: 1. Имя — 35 (31.5%) 2. ... 3. ...'."""
    if not categories:
        return f"{title}: нет данных"
    items = " ".join(
        f"{rank}. {entry['name']} — {entry['count']} ({entry['share']}%)"
        for rank, entry in enumerate(categories[:3], start=1)
    )
    return f"{title}: {items}"


def build_slack_summary(data: dict) -> str:
    """Текст сообщения в Slack из данных дашборда (build_dashboard_data)."""
    kpis = data.get("kpis", {})
    day_line = f"За сутки: {_plural_conversations(kpis.get('day_total', 0))} всего"
    change = _new_today_text(data.get("daily") or [])
    if change:
        day_line += f" {change}"
    return "\n".join([
        day_line,
        _top_categories_text("Топ-3 категорий за сутки", data.get("day_categories") or []),
        f"За неделю: {_plural_conversations(kpis.get('week_total', 0))} всего",
        _top_categories_text("Топ-3 категорий за неделю", data.get("week_categories") or []),
    ])


def _load_dashboard_data(dashboard_path: Path) -> dict | None:
    """Данные дашборда из JSON рядом с HTML; None, если файла нет или он битый."""
    try:
        return json.loads(dashboard_path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _check_ok(payload: dict, step: str) -> None:
    if not payload.get("ok"):
        raise SlackNotifyError(f"Slack {step}: {payload.get('error', 'неизвестная ошибка')}")


def send_dashboard(
    bot_token: str,
    channel_id: str,
    dashboard_path: str | Path,
    *,
    session=None,
) -> str:
    """Загружает dashboard.html в канал со сводкой из данных дашборда; возвращает file_id."""
    http = session or requests
    dashboard = Path(dashboard_path)
    content = dashboard.read_bytes()
    headers = {"Authorization": f"Bearer {bot_token}"}

    upload = http.post(
        f"{SLACK_API}/files.getUploadURLExternal",
        headers=headers,
        data={"filename": dashboard.name, "length": len(content)},
        timeout=DEFAULT_TIMEOUT,
    ).json()
    _check_ok(upload, "files.getUploadURLExternal")

    http.post(upload["upload_url"], data=content, timeout=DEFAULT_TIMEOUT).raise_for_status()

    data = _load_dashboard_data(dashboard)
    comment = build_slack_summary(data) if data else ""
    complete = http.post(
        f"{SLACK_API}/files.completeUploadExternal",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "files": [{"id": upload["file_id"], "title": dashboard.name}],
            "channel_id": channel_id,
            "initial_comment": comment,
        },
        timeout=DEFAULT_TIMEOUT,
    ).json()
    _check_ok(complete, "files.completeUploadExternal")
    return complete["files"][0]["id"]

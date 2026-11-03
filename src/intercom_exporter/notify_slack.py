"""Отправка dashboard.html и краткой сводки отчёта в Slack-канал.

Использует Bot Token (xoxb-...) и новый flow загрузки файлов:
files.getUploadURLExternal → POST содержимого → files.completeUploadExternal.
"""

from __future__ import annotations

from pathlib import Path

import requests

SLACK_API = "https://slack.com/api"
DEFAULT_TIMEOUT = 30


class SlackNotifyError(RuntimeError):
    pass


def _extract_summary(report_path: str | Path) -> str:
    """Первый непустой блок после заголовка '## Резюме' в report.md (сводка недели)."""
    try:
        lines = Path(report_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for index, line in enumerate(lines):
        if line.strip() != "## Резюме":
            continue
        summary = []
        for follow in lines[index + 1:]:
            if not follow.strip():
                if summary:
                    break
                continue
            if follow.startswith("#"):
                break
            summary.append(follow.strip())
        if summary:
            return " ".join(summary)
    return ""


def _check_ok(payload: dict, step: str) -> None:
    if not payload.get("ok"):
        raise SlackNotifyError(f"Slack {step}: {payload.get('error', 'неизвестная ошибка')}")


def send_dashboard(
    bot_token: str,
    channel_id: str,
    dashboard_path: str | Path,
    report_path: str | Path | None = None,
    *,
    session=None,
) -> str:
    """Загружает dashboard.html в канал со сводкой из report.md; возвращает file_id."""
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

    comment = _extract_summary(report_path) if report_path else ""
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

"""Единое правило осмысленной активности обращения.

Активностью считаются только содержательные части переписки (comment, open,
note, close) и само создание обращения. Поле updated_at и системные события
(bulk_reassignment, assignment, conversation_attribute_updated_by_admin,
snoozed, timer_unsnooze и прочие) активностью не считаются: массовые
админские операции обновляют updated_at тысяч обращений разом, хотя клиент
ничего не писал. Правило используют и exporter (отбор при выгрузке), и
monitoring (срезы неделя/день). Менять только синхронно с README.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

MEANINGFUL_PART_TYPES = frozenset({"comment", "open", "note", "close"})


def _timestamp_epoch(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp())


def _in_range(value: Any, start: int, end: int) -> bool:
    timestamp = _timestamp_epoch(value)
    return timestamp is not None and start <= timestamp < end


def has_meaningful_activity(conversation: dict[str, Any], start: int, end: int) -> bool:
    """True, если обращение создано в окне или имеет содержательную часть в окне."""
    if _in_range(conversation.get("created_at"), start, end):
        return True
    parts = conversation.get("conversation_parts") or []
    if isinstance(parts, dict):
        parts = parts.get("conversation_parts") or []
    if not isinstance(parts, list):
        return False
    return any(
        isinstance(part, dict)
        and part.get("part_type") in MEANINGFUL_PART_TYPES
        and _in_range(part.get("created_at"), start, end)
        for part in parts
    )

"""Детерминированная генерация интерактивного HTML-дашборда по результатам мониторинга."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .support_analysis import _write_text

PALETTE = [
    "#087f82", "#08a9df", "#f2a900", "#e4572e", "#7c5cbf", "#4caf7d",
    "#c94f7c", "#5b8def", "#8d6e63", "#607d8b", "#a3a948", "#d76d77",
]
SEVERITY_ORDER = ("high", "medium", "low", "unknown")


def _iso_date(value: Any) -> date | None:
    """Дата из ISO-строки ('2026-11-02T13:42:08Z' или '2026-11-02'); None, если не парсится."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _daily_series(week_rows: list[dict]) -> list[dict]:
    """Счётчики по дням за 7 календарных дней, заканчивая датой последней активности (updated_at, fallback created_at)."""
    dates = []
    for row in week_rows:
        day = _iso_date(row.get("updated_at")) or _iso_date(row.get("created_at"))
        if day:
            dates.append(day)
    if not dates:
        return []
    end = max(dates)
    start = end - timedelta(days=6)
    counts = Counter(day for day in dates if start <= day <= end)
    return [
        {"date": (start + timedelta(days=offset)).isoformat(),
         "count": counts.get(start + timedelta(days=offset), 0)}
        for offset in range(7)
    ]


def _category_breakdown(classifications: list[dict]) -> list[dict]:
    total = len(classifications)
    counts = Counter(item["category"] for item in classifications)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    return [
        {"name": name, "count": count, "share": round(count / total * 100, 1) if total else 0.0}
        for name, count in ranked
    ]


def _severity_breakdown(classifications: list[dict]) -> dict:
    counts = Counter(item["severity"] or "unknown" for item in classifications)
    return {key: counts.get(key, 0) for key in SEVERITY_ORDER}


def build_dashboard_data(
    week_rows: list[dict],
    day_rows: list[dict],
    categories: list[dict],
    classifications: list[dict],
    week_period: str,
    day_period: str,
) -> dict:
    """Собирает все агрегаты дашборда из провалидированных данных report-monitoring.

    Параметр ``categories`` принимается для совместимости интерфейса с пайплайном
    отчёта и зарезервирован на будущее — в агрегации он не используется.
    """
    week_ids = {str(row["id"]) for row in week_rows}
    day_ids = {str(row["id"]) for row in day_rows}
    week_classifications = [item for item in classifications if item["id"] in week_ids]
    day_classifications = [item for item in classifications if item["id"] in day_ids]
    week_categories = _category_breakdown(week_classifications)
    subcategory_counts = Counter(item["subcategory"] for item in week_classifications)
    subcategories = [
        {"name": name, "count": count}
        for name, count in sorted(subcategory_counts.items(), key=lambda item: (-item[1], item[0].casefold()))[:10]
    ]
    severity_week = _severity_breakdown(week_classifications)
    high_cases = [
        {"id": item["id"], "category": item["category"], "subcategory": item["subcategory"],
         "problem_summary": item["problem_summary"]}
        for item in week_classifications
        if item["severity"] == "high"
    ]
    top = week_categories[0] if week_categories else {"name": "—", "count": 0, "share": 0.0}
    cancel_week = sum(
        entry["count"] for entry in week_categories
        if "отмен" in entry["name"].casefold() or "возврат" in entry["name"].casefold()
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": {"period_label": week_period, "count": len(week_rows)},
        "day": {"period_label": day_period, "count": len(day_rows)},
        "kpis": {
            "week_total": len(week_rows),
            "day_total": len(day_rows),
            "top_category": top["name"],
            "top_category_share": top["share"],
            "high_week": severity_week["high"],
            "cancel_week": cancel_week,
        },
        "daily": _daily_series(week_rows),
        "week_categories": week_categories,
        "day_categories": _category_breakdown(day_classifications),
        "subcategories": subcategories,
        "severity": {"week": severity_week, "day": _severity_breakdown(day_classifications)},
        "high_cases": high_cases,
        "palette": PALETTE,
    }

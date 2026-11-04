"""Детерминированная генерация интерактивного HTML-дашборда по результатам мониторинга."""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape as _esc
from pathlib import Path
from typing import Any

from .agents_orchestration import BILLING_CATEGORY
from .services import SERVICES
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
    """Новые обращения по дням за 7 календарных дней, заканчивая днём последнего создания.

    Считается только дата создания (created_at): старые обращения с новой
    активностью на график не попадают. Поле updated_at не используется —
    его вздувают системные события (bulk_reassignment и т.п.).
    """
    dates = []
    for row in week_rows:
        day = _iso_date(row.get("created_at"))
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
    recommendations: dict | None = None,
    billing_category: str | None = None,
) -> dict:
    """Собирает все агрегаты дашборда из провалидированных данных report-monitoring.

    Параметр ``categories`` (элементы с ключами ``name`` и ``description``) попадает
    в данные дашборда как расшифровка категорий.

    Параметр ``recommendations`` — необязательный словарь со списками строк по ключам
    ``overall``, ``week``, ``day`` (рекомендации из результата анализа).

    Элементы ``classifications`` должны быть предварительно провалидированы пайплайном
    отчёта и содержать ключи ``id``, ``category``, ``subcategory``, ``problem_summary``,
    ``severity`` — ``KeyError`` при отсутствии ключей является осознанным контрактом входа.
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
    high_cases_all = [
        {"id": item["id"], "category": item["category"], "subcategory": item["subcategory"],
         "problem_summary": item["problem_summary"]}
        for item in week_classifications
        if item["severity"] == "high"
    ]
    high_cases_all.sort(key=lambda case: str(case["id"]) not in day_ids)
    high_cases = high_cases_all[:10]
    top = week_categories[0] if week_categories else {"name": "—", "count": 0, "share": 0.0}
    billing_week = sum(
        entry["count"] for entry in week_categories
        if entry["name"] == (billing_category or BILLING_CATEGORY)
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
            "billing_week": billing_week,
        },
        "daily": _daily_series(week_rows),
        "week_categories": week_categories,
        "day_categories": _category_breakdown(day_classifications),
        "subcategories": subcategories,
        "severity": {"week": severity_week, "day": _severity_breakdown(day_classifications)},
        "high_cases": high_cases,
        "high_cases_total": len(high_cases_all),
        "categories": [{"name": item["name"], "description": item["description"]} for item in categories],
        "recommendations": {
            key: list((recommendations or {}).get(key) or [])
            for key in ("overall", "week", "day")
        },
        "palette": PALETTE,
    }

def _render_daily_chart(rows: list[dict]) -> str:
    if not rows:
        return "Нет данных по датам"
    W, H, PL, PR, PT, PB = 900, 280, 56, 24, 24, 48
    max_count = max(r["count"] for r in rows) or 1
    raw = max_count / 4
    mag = 10 ** math.floor(math.log10(raw))
    norm = raw / mag
    tick = (1 if norm <= 1 else 2 if norm <= 2 else 5 if norm <= 5 else 10) * mag
    y_max = tick * math.ceil(max_count / tick)

    def x(index: int) -> float:
        return PL + index * (W - PL - PR) / (len(rows) - 1 or 1)

    def y(value: float) -> float:
        return PT + (1 - value / y_max) * (H - PT - PB)

    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%">']
    value = 0
    while value <= y_max:
        stroke = "#e3ecef" if value else "#c3d3d9"
        parts.append(
            f'<line x1="{PL}" y1="{y(value)}" x2="{W - PR}" y2="{y(value)}" stroke="{stroke}" stroke-width="1"/>'
            f'<text x="{PL - 10}" y="{y(value) + 5}" text-anchor="end" font-size="13" fill="#6b7f88">{value:g}</text>'
        )
        value += tick
    mid_y = PT + (H - PT - PB) / 2
    parts.append(
        f'<line x1="{PL}" y1="{PT}" x2="{PL}" y2="{H - PB}" stroke="#c3d3d9" stroke-width="1"/>'
        f'<text x="16" y="{mid_y}" text-anchor="middle" font-size="13" fill="#6b7f88" '
        f'transform="rotate(-90 16 {mid_y})">Обращений</text>'
    )
    points = " ".join(f"{x(i)},{y(row['count'])}" for i, row in enumerate(rows))
    parts.append(f'<polyline points="{points}" fill="none" stroke="{PALETTE[0]}" stroke-width="2.5"/>')
    for i, row in enumerate(rows):
        day_label = f"{row['date'][8:10]}.{row['date'][5:7]}"
        parts.append(f'<circle cx="{x(i)}" cy="{y(row["count"])}" r="4" fill="{PALETTE[1]}"/>')
        if row["count"]:
            parts.append(f'<text x="{x(i)}" y="{y(row["count"]) - 10}" text-anchor="middle" font-size="13">{row["count"]}</text>')
        parts.append(f'<text x="{x(i)}" y="{H - PB + 20}" text-anchor="middle" font-size="13" fill="#6b7f88">{day_label}</text>')
    parts.append(f'<text x="{PL + (W - PL - PR) / 2}" y="{H - 6}" text-anchor="middle" font-size="13" fill="#6b7f88">Дата</text>')
    parts.append("</svg>")
    return "".join(parts)


def _render_donut(rows: list[dict]) -> tuple[str, str]:
    total = sum(r["count"] for r in rows)
    if not total:
        return "Нет данных", ""
    R = 70
    circumference = 2 * math.pi * R
    offset = 0.0
    arcs = []
    legend = []
    for index, row in enumerate(rows):
        length = row["count"] / total * circumference
        color = PALETTE[index % len(PALETTE)]
        arcs.append(
            f'<circle r="{R}" cx="100" cy="100" fill="none" stroke="{color}" stroke-width="26" '
            f'stroke-dasharray="{length:.3f} {circumference - length:.3f}" stroke-dashoffset="{-offset:.3f}" '
            f'transform="rotate(-90 100 100)"><title>{_esc(row["name"])}: {row["count"]}</title></circle>'
        )
        legend.append(
            f'<span><span class="dot" style="background:{color}"></span>'
            f'{_esc(row["name"])} — {row["count"]} ({row["share"]}%)</span>'
        )
        offset += length
    svg = (
        f'<svg viewBox="0 0 200 200" width="200" style="display:block;margin:0 auto">{"".join(arcs)}'
        f'<text x="100" y="96" text-anchor="middle" font-size="26" font-weight="600">{total}</text>'
        f'<text x="100" y="116" text-anchor="middle" font-size="11" fill="#6b7f88">обращений</text></svg>'
    )
    return svg, "".join(legend)


def _render_bars(rows: list[dict]) -> str:
    if not rows:
        return "Нет данных"
    max_count = max(r["count"] for r in rows) or 1
    parts = []
    for index, row in enumerate(rows):
        width = row["count"] / max_count * 100
        color = PALETTE[index % len(PALETTE)]
        parts.append(
            f'<div class="bar-row"><span>{_esc(row["name"])}</span>'
            f'<div class="bar-bg"><div class="bar" style="width:{width:.1f}%;background:{color}"></div></div>'
            f'<b>{row["count"]}</b></div>'
        )
    return "".join(parts)


_SEVERITY_LABELS = {"high": "Высокая", "medium": "Средняя", "low": "Низкая", "unknown": "Не оценена"}
_SEVERITY_COLORS = {"high": "#e4572e", "medium": "#f2a900", "low": "#4caf7d", "unknown": "#b0bec5"}


def _render_severity(severity: dict) -> str:
    parts = []
    for key, title in (("week", "Неделя"), ("day", "День")):
        values = severity.get(key, {})
        total = sum(values.values()) or 1
        bar = "".join(
            f'<div style="width:{values[key2] / total * 100:.1f}%;background:{_SEVERITY_COLORS[key2]}" '
            f'title="{_SEVERITY_LABELS[key2]}: {values[key2]}"></div>'
            for key2 in _SEVERITY_LABELS
            if values.get(key2)
        )
        text = " · ".join(f"{_SEVERITY_LABELS[key2]}: {values.get(key2, 0)}" for key2 in _SEVERITY_LABELS)
        parts.append(
            f'<h3 style="font-size:13px;margin:8px 0 4px">{title}</h3>'
            f'<div class="bar-bg" style="display:flex;height:16px">{bar}</div>'
            f'<div style="font-size:12px;color:#6b7f88;margin-top:4px">{text}</div>'
        )
    return "".join(parts)


def _render_high_cases(data: dict) -> str:
    cases = data["high_cases"]
    if not cases:
        return "Обращений высокой тяжести нет."
    shown = len(cases)
    total = data.get("high_cases_total") or shown
    note = (
        f'<div style="font-size:12px;color:#6b7f88;margin-bottom:6px">'
        f'Показано {shown} из {total} обращений высокой тяжести за неделю.</div>'
        if total > shown else ""
    )
    rows = "".join(
        f'<tr><td>{_esc(case["id"])}</td><td>{_esc(case["category"])}</td>'
        f'<td>{_esc(case["subcategory"])}</td><td>{_esc(case["problem_summary"])}</td></tr>'
        for case in cases
    )
    return note + "<table><tr><th>ID</th><th>Категория</th><th>Подкатегория</th><th>Суть проблемы</th></tr>" + rows + "</table>"


def _render_recommendations(recommendations: dict) -> str:
    items = recommendations.get("overall") or []
    if not items:
        return "Рекомендации не сформированы."
    return '<ul style="padding-left:18px;font-size:13px">' + "".join(
        f'<li style="margin:3px 0">{_esc(item)}</li>' for item in items
    ) + "</ul>"


def _render_categories(categories: list[dict]) -> str:
    if not categories:
        return "Нет данных о категориях."
    rows = "".join(
        f'<tr><td>{_esc(item["name"])}</td><td>{_esc(item["description"])}</td></tr>'
        for item in categories
    )
    return "<table><tr><th>Категория</th><th>Описание</th></tr>" + rows + "</table>"


def _render_kpis(data: dict) -> str:
    kpis = [
        ("Обращений за неделю", data["kpis"]["week_total"], ""),
        ("Обращений за день", data["kpis"]["day_total"], ""),
        ("Топ-категория недели", data["kpis"]["top_category"], f'{data["kpis"]["top_category_share"]}% недели'),
        ("Высокая тяжесть (неделя)", data["kpis"]["high_week"], ""),
        ("Оплата и подписка (неделя)", data["kpis"]["billing_week"], ""),
    ]
    return "".join(
        f'<div class="kpi"><div class="label">{_esc(str(label))}</div>'
        f'<div class="value">{_esc(str(value))}</div><div class="sub">{_esc(str(sub))}</div></div>'
        for label, value, sub in kpis
    )


_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Мониторинг обращений поддержки</title>
<style>
:root{--teal:#087f82;--blue:#08a9df;--bg:#f3f7f8;--card:#fff;--text:#1d2b33;--muted:#6b7f88;--border:#e3ecef}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI",Arial,sans-serif;background:var(--bg);color:var(--text);padding:24px;font-size:16px}
.wrap{max-width:1200px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-bottom:20px}
h1{font-size:26px}
.badge{background:var(--teal);color:#fff;border-radius:12px;padding:2px 10px;font-size:13px}
.period{color:var(--muted);font-size:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:20px}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px}
.kpi .label{font-size:13px;color:var(--muted);text-transform:uppercase}
.kpi .value{font-size:26px;font-weight:600;margin-top:4px}
.kpi .sub{font-size:13px;color:var(--muted)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.grid .card:first-child{grid-column:1/-1}
@media (max-width:720px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px}
.card h2{font-size:17px;margin-bottom:12px}
.legend{display:flex;flex-wrap:wrap;gap:8px;font-size:14px;margin-top:10px}
.donut-card{display:flex;flex-wrap:wrap;align-items:center}
.donut-card h2{width:100%}
.donut-card .legend{flex:1;flex-direction:column;align-items:flex-start;margin-top:0}
.legend span{display:inline-flex;align-items:center;gap:4px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.bar-row{display:grid;grid-template-columns:minmax(120px,220px) 1fr 48px;gap:8px;align-items:center;font-size:14px;margin:5px 0}
.bar{height:14px;border-radius:7px}
.bar-bg{background:#eef4f6;border-radius:7px;overflow:hidden}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--muted);font-weight:600}
footer{margin-top:20px;color:var(--muted);font-size:13px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Мониторинг обращений поддержки</h1>
    <span class="badge">__GENERATED__</span>
    <span class="period">__PERIODS__</span>
  </header>
  <div class="kpis">__KPIS__</div>
  <div class="grid">
    <div class="card"><h2>Новые обращения по дням (неделя)</h2>__CHART_DAILY__
      <div style="font-size:12px;color:#6b7f88;margin-top:6px">Столбцы — целые даты, только новые обращения (по дате создания). Старые обращения с новой активностью здесь не считаются. KPI «день» — скользящие 24 часа от момента выгрузки, поэтому последний столбец может немного отличаться.</div>
    </div>
    <div class="card donut-card"><h2>Категории — всего за неделю</h2>__DONUT_WEEK__<div class="legend">__LEGEND_WEEK__</div></div>
    <div class="card donut-card"><h2>Категории — всего за сутки</h2>__DONUT_DAY__<div class="legend">__LEGEND_DAY__</div></div>
    <div class="card"><h2>Топ-10 подкатегорий (неделя)</h2>__BARS_SUB__</div>
    <div class="card"><h2>Тяжесть обращений</h2>__SEVERITY__</div>
  </div>
  <div class="card" style="margin-top:14px"><h2>Топ-10 обращений высокой тяжести (неделя)</h2>__HIGH__</div>
  <div class="card" style="margin-top:14px"><h2>Рекомендации</h2>__RECOMMENDATIONS__</div>
  <div class="card" style="margin-top:14px"><h2>Расшифровка категорий</h2>__CATEGORIES__</div>
  <div class="card" style="margin-top:14px"><h2>Что такое тяжесть</h2>
    <table>
      <tr><th>Уровень</th><th>Значение</th></tr>
      <tr><td>Высокая (high)</td><td>Явно подтверждённое существенное длительное нарушение критичного сценария с описанным влиянием на клиента.</td></tr>
      <tr><td>Средняя (medium)</td><td>Прямо описанная невозможность использовать значимую функцию, заметные перебои или повторяющиеся нарушения.</td></tr>
      <tr><td>Низкая (low)</td><td>Вопрос, настройка, небольшое неудобство без серьёзного заявленного эффекта.</td></tr>
      <tr><td>Не оценена</td><td>В тексте обращения недостаточно данных о влиянии на клиента.</td></tr>
    </table>
    <p style="margin-top:10px;font-size:12px;color:var(--muted)">Тяжесть — качественная оценка по тексту обращений: учитывается только влияние, описанное самим клиентом. Она не измеряет число затронутых клиентов, финансовый ущерб или технический масштаб проблемы.</p>
  </div>
  <footer>Сгенерировано автоматически: analyze_support.py report-monitoring</footer>
</div>
</body>
</html>
"""


def render_dashboard_html(data: dict, prefix: str = "") -> str:
    """Возвращает самодостаточный офлайн HTML с внедрёнными данными (без внешних ресурсов).

    Страница полностью статичная: графики и таблицы генерируются на стороне
    Python в SVG/HTML, поэтому дашборд отображается и в превью Slack, где
    JavaScript не выполняется. ``prefix`` сохранён в сигнатуре для совместимости.
    """
    donut_week, legend_week = _render_donut(data["week_categories"])
    donut_day, legend_day = _render_donut(data["day_categories"])
    generated_dt = datetime.fromisoformat(data["generated_at"])
    generated = "Обновлено " + generated_dt.strftime("%d.%m.%Y %H:%M")
    periods = f"Неделя: {data['week']['period_label']} · День: {data['day']['period_label']}"
    return (
        _TEMPLATE
        .replace("__GENERATED__", _esc(generated))
        .replace("__PERIODS__", _esc(periods))
        .replace("__KPIS__", _render_kpis(data))
        .replace("__CHART_DAILY__", _render_daily_chart(data["daily"]))
        .replace("__DONUT_WEEK__", donut_week)
        .replace("__LEGEND_WEEK__", legend_week)
        .replace("__DONUT_DAY__", donut_day)
        .replace("__LEGEND_DAY__", legend_day)
        .replace("__BARS_SUB__", _render_bars(data["subcategories"]))
        .replace("__SEVERITY__", _render_severity(data["severity"]))
        .replace("__HIGH__", _render_high_cases(data))
        .replace("__RECOMMENDATIONS__", _render_recommendations(data["recommendations"]))
        .replace("__CATEGORIES__", _render_categories(data["categories"]))
    )


def write_dashboard(data: dict, output_dir: str | Path) -> Path:
    """Атомно записывает dashboard_DD_MM_YYYY.html (дата генерации, UTC) в output_dir.

    Рядом пишет dashboard_DD_MM_YYYY.json с теми же данными — из него
    notify_slack строит текст сообщения в Slack.
    """
    stamp = datetime.now(timezone.utc).strftime("%d_%m_%Y")
    output_path = Path(output_dir) / f"dashboard_{stamp}.html"
    _write_text(output_path, render_dashboard_html(data))
    _write_text(output_path.with_suffix(".json"),
                json.dumps(data, ensure_ascii=False, indent=2))
    return output_path


_COMBINED_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Мониторинг обращений — VPNНаоборот и HyperTube</title>
<style>
:root{--teal:#087f82;--blue:#08a9df;--bg:#f3f7f8;--card:#fff;--text:#1d2b33;--muted:#6b7f88;--border:#e3ecef}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI",Arial,sans-serif;background:var(--bg);color:var(--text);padding:24px;font-size:16px}
.wrap{max-width:1200px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-bottom:20px}
h1{font-size:26px}
.badge{background:var(--teal);color:#fff;border-radius:12px;padding:2px 10px;font-size:13px}
.tabs{display:flex;gap:8px;margin-bottom:12px;border-bottom:1px solid var(--border);padding-bottom:8px}
.tab{display:inline-block;background:#fff;border:1px solid var(--border);border-radius:8px;padding:8px 16px;font-size:14px;color:var(--text);text-decoration:none}
.tab:hover{background:var(--teal);color:#fff;border-color:var(--teal)}
.service-section{scroll-margin-top:16px}
.service-section h2{font-size:22px;margin:24px 0 12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:20px}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px}
.kpi .label{font-size:13px;color:var(--muted);text-transform:uppercase}
.kpi .value{font-size:26px;font-weight:600;margin-top:4px}
.kpi .sub{font-size:13px;color:var(--muted)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.grid .card:first-child{grid-column:1/-1}
@media (max-width:720px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px}
.card h2{font-size:17px;margin-bottom:12px}
.legend{display:flex;flex-wrap:wrap;gap:8px;font-size:14px;margin-top:10px}
.donut-card{display:flex;flex-wrap:wrap;align-items:center}
.donut-card h2{width:100%}
.donut-card .legend{flex:1;flex-direction:column;align-items:flex-start;margin-top:0}
.legend span{display:inline-flex;align-items:center;gap:4px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.bar-row{display:grid;grid-template-columns:minmax(120px,220px) 1fr 48px;gap:8px;align-items:center;font-size:14px;margin:5px 0}
.bar{height:14px;border-radius:7px}
.bar-bg{background:#eef4f6;border-radius:7px;overflow:hidden}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--muted);font-weight:600}
footer{margin-top:20px;color:var(--muted);font-size:13px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <header><h1>Мониторинг обращений поддержки</h1><span class="badge">__GENERATED_AT__</span></header>
  <div class="tabs">__TABS__</div>
  __SECTIONS__
  <footer>Сгенерировано автоматически: analyze_support.py research</footer>
</div>
</body>
</html>
"""


def _extract_body_inner(html: str) -> str:
    """Возвращает содержимое <body>...</body> без тегов <html>/<body>."""
    start = html.index("<body>") + len("<body>")
    end = html.index("</body>")
    return html[start:end]


def write_combined_dashboard(
    services_data: dict[str, dict],
    services_html: dict[str, str] | None,
    output_dir: str | Path,
) -> Path:
    """Собирает общий статичный дашборд из секций каждого сервиса.

    Страница не использует JavaScript и iframe: секции идут подряд,
    навигация — якорные ссылки. Такой HTML отображается в превью Slack,
    где скрипты и data: URL заблокированы. ``services_html`` сохранён
    в сигнатуре для совместимости, но не используется.
    """
    stamp = datetime.now(timezone.utc).strftime("%d_%m_%Y")
    output_path = Path(output_dir) / f"dashboard_{stamp}.html"
    tabs = []
    sections = []
    for sid in SERVICES:
        if sid not in services_data:
            continue
        label = SERVICES[sid].label
        tabs.append(f'<a class="tab" href="#{sid}">{label}</a>')
        single = render_dashboard_html(services_data[sid])
        body = _extract_body_inner(single)
        sections.append(f'<div id="{sid}" class="service-section"><h2>{label}</h2>{body}</div>')
    html = _COMBINED_TEMPLATE.replace("__TABS__", "".join(tabs)) \
                           .replace("__SECTIONS__", "".join(sections)) \
                           .replace("__GENERATED_AT__", datetime.now(timezone.utc).isoformat(timespec="minutes"))
    _write_text(output_path, html)
    _write_text(output_path.with_suffix(".json"),
                json.dumps(services_data, ensure_ascii=False, indent=2))
    return output_path

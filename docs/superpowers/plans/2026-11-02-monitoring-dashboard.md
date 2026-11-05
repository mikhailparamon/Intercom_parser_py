# Интерактивный дашборд мониторинга поддержки — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `report-monitoring` дополнительно генерирует самодостаточный интерактивный `dashboard.html` (KPI, график по дням, круговые диаграммы категорий за неделю и день, топ подкатегорий, тяжесть, high-кейсы).

**Architecture:** Дашборд строится детерминированным Python-генератором `src/intercom_exporter/dashboard.py` из уже провалидированных данных (`week_rows`, `day_rows`, `categories`, `classifications`) — без LLM, поэтому в `PROMPT.md` ничего добавлять не нужно; промпт остаётся ответственным только за классификации. Генератор встраивает данные как JSON в один офлайн HTML (vanilla JS + inline SVG, без CDN) в стиле референса `report_dashboard/dashboard.html`: светлая тема `#f3f7f8`, белые карточки, teal-акценты `#087f82`/`#08a9df`.

**Tech Stack:** Python 3 (существующий пакет `intercom_exporter`), pytest, один HTML-файл с inline CSS/JS.

**Согласованный состав дашборда** (дефолты, пользователь не возражал): шапка с периодами → KPI-карточки (всего за неделю, за день, топ-категория с долей, high severity, «Подписка и отмена») → линейный график по дням недели → два donut (неделя и день) → бары топ-10 подкатегорий → severity-распределение → таблица high-кейсов.

**Важно про данные:** у многих записей `created_at` старый (2023–2026), окна выбираются по активности — поэтому дневная серия графика считается по дате `updated_at` (fallback `created_at`) внутри недельного окна: `end = max(dates)`, `start = end − 6 дней`.

**Файлы:**
- Create: `src/intercom_exporter/dashboard.py`
- Modify: `src/intercom_exporter/support_analysis.py` (`generate_monitoring_report` строки 569–635, `build_parser` строки 656–661, `main` строки 730–732)
- Create: `tests/test_dashboard.py`
- Modify: `README.md` (раздел про report-monitoring)

**Запуск тестов:** `.venv/Scripts/python.exe -m pytest tests/test_dashboard.py -v` (Windows, из корня проекта).

---

### Task 1: Слой данных `build_dashboard_data` в `dashboard.py`

**Files:**
- Create: `src/intercom_exporter/dashboard.py`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write the failing test**

Создать `tests/test_dashboard.py`:

```python
from intercom_exporter.dashboard import build_dashboard_data

CATEGORIES = [
    {"name": "Подключение VPN", "description": "Проблемы с подключением"},
    {"name": "Оплата", "description": "Проблемы с оплатой"},
    {"name": "Подписка и отмена", "description": "Отмена подписки и возвраты"},
]


def _week_rows():
    return [
        {"id": "w1", "created_at": "2026-10-26T09:00:00Z", "updated_at": "2026-10-26T10:00:00Z"},
        {"id": "w2", "created_at": "2025-01-01T00:00:00Z", "updated_at": "2026-11-01T12:00:00Z"},
        {"id": "d1", "created_at": "2024-05-01T00:00:00Z", "updated_at": "2026-11-02T08:00:00Z"},
    ]


def _day_rows():
    return [_week_rows()[2]]


def _classification(conversation_id, category, subcategory, severity):
    return {
        "id": conversation_id,
        "category": category,
        "subcategory": subcategory,
        "problem_summary": f"Суть {conversation_id}",
        "severity": severity,
        "severity_reason": "причина",
        "confidence": "high",
        "evidence": "цитата",
    }


def _classifications():
    return [
        _classification("w1", "Подключение VPN", "Не подключается", "high"),
        _classification("w2", "Оплата", "Карта отклонена", "medium"),
        _classification("d1", "Подключение VPN", "Не подключается", None),
    ]


def test_build_dashboard_data_counts_windows_categories_and_severity():
    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, _classifications(), "week label", "day label")
    assert data["kpis"]["week_total"] == 3
    assert data["kpis"]["day_total"] == 1
    assert data["kpis"]["top_category"] == "Подключение VPN"
    assert data["kpis"]["top_category_share"] == 66.7
    assert data["kpis"]["high_week"] == 1
    assert data["kpis"]["cancel_week"] == 0
    assert data["week_categories"][0] == {"name": "Подключение VPN", "count": 2, "share": 66.7}
    assert data["day_categories"] == [{"name": "Подключение VPN", "count": 1, "share": 100.0}]
    assert data["severity"]["week"] == {"high": 1, "medium": 1, "low": 0, "unknown": 1}
    assert data["severity"]["day"] == {"high": 0, "medium": 0, "low": 0, "unknown": 1}
    assert data["subcategories"][0] == {"name": "Не подключается", "count": 2}
    assert [case["id"] for case in data["high_cases"]] == ["w1"]
    assert data["week"]["period_label"] == "week label"
    assert data["day"]["period_label"] == "day label"


def test_daily_series_covers_seven_days_ending_at_latest_activity():
    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, _classifications(), "w", "d")
    assert [item["date"] for item in data["daily"]] == [
        "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02",
    ]
    assert [item["count"] for item in data["daily"]] == [0, 0, 0, 0, 0, 1, 1]
    # w1 (2026-10-26) старше 7-дневного окна от max(2026-11-02) — не попадает в серию, но остаётся в week_total
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'intercom_exporter.dashboard'`

- [ ] **Step 3: Write minimal implementation**

Создать `src/intercom_exporter/dashboard.py` (пока без HTML — только слой данных; HTML добавляется в Task 2):

```python
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
    """Собирает все агрегаты дашборда из провалидированных данных report-monitoring."""
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/dashboard.py tests/test_dashboard.py
git commit -m "feat: dashboard data aggregation for monitoring report"
```

---

### Task 2: HTML-шаблон, `render_dashboard_html` и `write_dashboard`

**Files:**
- Modify: `src/intercom_exporter/dashboard.py` (добавить `_TEMPLATE`, `render_dashboard_html`, `write_dashboard`)
- Test: `tests/test_dashboard.py` (добавить тест)

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_dashboard.py`:

```python
from intercom_exporter.dashboard import render_dashboard_html, write_dashboard


def test_write_dashboard_creates_self_contained_offline_html(tmp_path):
    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, _classifications(), "w", "d")
    path = write_dashboard(data, tmp_path)
    assert path == tmp_path / "dashboard.html"
    html = path.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "Подключение VPN" in html
    assert '"week_total":3' in html.replace(" ", "")
    assert "http" not in html.split("<body")[0].replace("https://", "").replace("http://", "")  # нет CDN в <head>
    assert "const DATA = " in html
    assert "</script>" in html


def test_render_dashboard_html_escapes_script_closing_tag():
    categories = [{"name": "X</script>", "description": "d"}]
    rows = [{"id": "a", "updated_at": "2026-11-02T00:00:00Z"}]
    classifications = [_classification("a", "X</script>", "sub", None)]
    html = render_dashboard_html(build_dashboard_data(rows, rows, categories, classifications, "w", "d"))
    assert "X<\\/script>" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_dashboard_html'`

- [ ] **Step 3: Write the implementation**

Дописать в конец `src/intercom_exporter/dashboard.py`:

```python
_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Мониторинг обращений поддержки</title>
<style>
:root{--teal:#087f82;--blue:#08a9df;--bg:#f3f7f8;--card:#fff;--text:#1d2b33;--muted:#6b7f88;--border:#e3ecef}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI",Arial,sans-serif;background:var(--bg);color:var(--text);padding:24px}
.wrap{max-width:1200px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-bottom:20px}
h1{font-size:24px}
.badge{background:var(--teal);color:#fff;border-radius:12px;padding:2px 10px;font-size:12px}
.period{color:var(--muted);font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:20px}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px}
.kpi .label{font-size:12px;color:var(--muted);text-transform:uppercase}
.kpi .value{font-size:24px;font-weight:600;margin-top:4px}
.kpi .sub{font-size:12px;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px}
.card h2{font-size:15px;margin-bottom:12px}
.legend{display:flex;flex-wrap:wrap;gap:8px;font-size:12px;margin-top:10px}
.legend span{display:inline-flex;align-items:center;gap:4px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.bar-row{display:grid;grid-template-columns:minmax(120px,220px) 1fr 48px;gap:8px;align-items:center;font-size:13px;margin:5px 0}
.bar{height:14px;border-radius:7px}
.bar-bg{background:#eef4f6;border-radius:7px;overflow:hidden}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--muted);font-weight:600}
footer{margin-top:20px;color:var(--muted);font-size:12px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Мониторинг обращений поддержки</h1>
    <span class="badge" id="generated"></span>
    <span class="period" id="periods"></span>
  </header>
  <div class="kpis" id="kpis"></div>
  <div class="grid">
    <div class="card"><h2>Обращения по дням (неделя)</h2><div id="chart-daily"></div></div>
    <div class="card"><h2>Категории — неделя</h2><div id="donut-week"></div><div class="legend" id="legend-week"></div></div>
    <div class="card"><h2>Категории — день</h2><div id="donut-day"></div><div class="legend" id="legend-day"></div></div>
    <div class="card"><h2>Топ-10 подкатегорий (неделя)</h2><div id="bars-sub"></div></div>
    <div class="card"><h2>Тяжесть обращений</h2><div id="severity"></div></div>
  </div>
  <div class="card" style="margin-top:14px"><h2>Обращения высокой тяжести (неделя)</h2><div id="high"></div></div>
  <footer>Сгенерировано автоматически: analyze_support.py report-monitoring</footer>
</div>
<script>
const DATA = __DASHBOARD_DATA__;
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const PAL = DATA.palette;
document.getElementById("generated").textContent = "Обновлено " + new Date(DATA.generated_at).toLocaleString("ru-RU");
document.getElementById("periods").textContent = "Неделя: " + DATA.week.period_label + " · День: " + DATA.day.period_label;

const kpis = [
  ["Обращений за неделю", DATA.kpis.week_total, ""],
  ["Обращений за день", DATA.kpis.day_total, ""],
  ["Топ-категория недели", DATA.kpis.top_category, DATA.kpis.top_category_share + "% недели"],
  ["Высокая тяжесть (неделя)", DATA.kpis.high_week, ""],
  ["Подписка и отмена (неделя)", DATA.kpis.cancel_week, ""],
];
document.getElementById("kpis").innerHTML = kpis.map(([label, value, sub]) =>
  `<div class="kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="sub">${esc(sub)}</div></div>`).join("");

(function () {
  const el = document.getElementById("chart-daily");
  const rows = DATA.daily;
  if (!rows.length) { el.textContent = "Нет данных по датам"; return; }
  const W = 560, H = 220, P = 34;
  const max = Math.max(...rows.map(r => r.count), 1);
  const x = i => P + i * (W - 2 * P) / (rows.length - 1 || 1);
  const y = v => H - P - (v / max) * (H - 2 * P);
  const pts = rows.map((r, i) => `${x(i)},${y(r.count)}`).join(" ");
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%"><polyline points="${pts}" fill="none" stroke="${PAL[0]}" stroke-width="2.5"/>`;
  rows.forEach((r, i) => {
    svg += `<circle cx="${x(i)}" cy="${y(r.count)}" r="4" fill="${PAL[1]}"/>` +
      `<text x="${x(i)}" y="${y(r.count) - 9}" text-anchor="middle" font-size="12">${r.count}</text>` +
      `<text x="${x(i)}" y="${H - P + 16}" text-anchor="middle" font-size="11" fill="#6b7f88">${r.date.slice(5)}</text>`;
  });
  el.innerHTML = svg + "</svg>";
})();

function donut(elId, legendId, rows) {
  const el = document.getElementById(elId), legend = document.getElementById(legendId);
  const total = rows.reduce((s, r) => s + r.count, 0);
  if (!total) { el.textContent = "Нет данных"; return; }
  const R = 70, C = 2 * Math.PI * R;
  let offset = 0, arcs = "", legendHtml = "";
  rows.forEach((r, i) => {
    const len = r.count / total * C, color = PAL[i % PAL.length];
    arcs += `<circle r="${R}" cx="100" cy="100" fill="none" stroke="${color}" stroke-width="26" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-offset}" transform="rotate(-90 100 100)"><title>${esc(r.name)}: ${r.count}</title></circle>`;
    legendHtml += `<span><span class="dot" style="background:${color}"></span>${esc(r.name)} — ${r.count} (${r.share}%)</span>`;
    offset += len;
  });
  el.innerHTML = `<svg viewBox="0 0 200 200" width="200" style="display:block;margin:0 auto">${arcs}<text x="100" y="96" text-anchor="middle" font-size="26" font-weight="600">${total}</text><text x="100" y="116" text-anchor="middle" font-size="11" fill="#6b7f88">обращений</text></svg>`;
  legend.innerHTML = legendHtml;
}
donut("donut-week", "legend-week", DATA.week_categories);
donut("donut-day", "legend-day", DATA.day_categories);

(function () {
  const rows = DATA.subcategories;
  const max = Math.max(...rows.map(r => r.count), 1);
  document.getElementById("bars-sub").innerHTML = rows.map((r, i) =>
    `<div class="bar-row"><span>${esc(r.name)}</span><div class="bar-bg"><div class="bar" style="width:${(r.count / max * 100).toFixed(1)}%;background:${PAL[i % PAL.length]}"></div></div><b>${r.count}</b></div>`).join("");
})();

(function () {
  const labels = { high: "Высокая", medium: "Средняя", low: "Низкая", unknown: "Не оценена" };
  const colors = { high: "#e4572e", medium: "#f2a900", low: "#4caf7d", unknown: "#b0bec5" };
  document.getElementById("severity").innerHTML = ["week", "day"].map(key => {
    const sev = DATA.severity[key];
    const total = Object.values(sev).reduce((a, b) => a + b, 0) || 1;
    const bar = Object.keys(labels).map(k => sev[k] ? `<div style="width:${(sev[k] / total * 100).toFixed(1)}%;background:${colors[k]}" title="${labels[k]}: ${sev[k]}"></div>` : "").join("");
    const text = Object.keys(labels).map(k => `${labels[k]}: ${sev[k]}`).join(" · ");
    return `<h3 style="font-size:13px;margin:8px 0 4px">${key === "week" ? "Неделя" : "День"}</h3><div class="bar-bg" style="display:flex;height:16px">${bar}</div><div style="font-size:12px;color:#6b7f88;margin-top:4px">${text}</div>`;
  }).join("");
})();

(function () {
  const el = document.getElementById("high");
  if (!DATA.high_cases.length) { el.textContent = "Обращений высокой тяжести нет."; return; }
  el.innerHTML = "<table><tr><th>ID</th><th>Категория</th><th>Подкатегория</th><th>Суть проблемы</th></tr>" +
    DATA.high_cases.map(c => `<tr><td>${esc(c.id)}</td><td>${esc(c.category)}</td><td>${esc(c.subcategory)}</td><td>${esc(c.problem_summary)}</td></tr>`).join("") + "</table>";
})();
</script>
</body>
</html>
"""


def render_dashboard_html(data: dict) -> str:
    """Возвращает самодостаточный офлайн HTML с внедрёнными данными (без внешних ресурсов)."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return _TEMPLATE.replace("__DASHBOARD_DATA__", payload)


def write_dashboard(data: dict, output_dir: str | Path) -> Path:
    """Атомарно записывает dashboard.html в output_dir и возвращает путь."""
    output_path = Path(output_dir) / "dashboard.html"
    _write_text(output_path, render_dashboard_html(data))
    return output_path
```

Замечание: `_TEMPLATE` — обычная строка (не f-string), поэтому JS-фигурные скобки не экранируются; данные подставляются через `.replace("__DASHBOARD_DATA__", ...)`. Экранирование `</` → `<\/` в `render_dashboard_html` не даёт JSON сломать inline `<script>`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/dashboard.py tests/test_dashboard.py
git commit -m "feat: offline interactive dashboard.html renderer"
```

---

### Task 3: Интеграция в `generate_monitoring_report` и CLI `--dashboard-dir`

**Files:**
- Modify: `src/intercom_exporter/support_analysis.py` (строки 569–635, 656–661, 730–732)
- Test: `tests/test_dashboard.py` (добавить интеграционный тест)

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_dashboard.py`:

```python
import json

from intercom_exporter.support_analysis import _build_payload, generate_monitoring_report


def _write_window(directory, rows):
    directory.mkdir(parents=True)
    (directory / "conversations.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    (directory / "manifest.json").write_text(
        json.dumps({"start": "2026-10-26", "end_exclusive": "2026-11-02"}), encoding="utf-8")
    return directory / "conversations.json"


def test_generate_monitoring_report_writes_dashboard(tmp_path):
    week_rows = _week_rows()
    day_rows = _day_rows()
    week_path = _write_window(tmp_path / "run" / "last_week", week_rows)
    day_path = _write_window(tmp_path / "run" / "last_day", day_rows)
    union_payload = _build_payload(week_rows)  # day_rows ⊂ week_rows по id
    result = {
        "complete": True,
        "conversation_count": 3,
        "dataset_fingerprint": union_payload["dataset_fingerprint"],
        "categories": CATEGORIES,
        "classifications": _classifications(),
        "executive_summary": "Сводка",
        "recommendations": ["Рекомендация"],
        "period_analysis": {
            "week": {"conversation_count": 3, "dataset_fingerprint": union_payload["dataset_fingerprint"],
                     "executive_summary": "Неделя", "recommendations": ["..."]},
            "day": {"conversation_count": 1, "dataset_fingerprint": _build_payload(day_rows)["dataset_fingerprint"],
                    "executive_summary": "День", "recommendations": ["..."]},
        },
    }
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    dashboard_dir = tmp_path / "dashboard"

    outcome = generate_monitoring_report(week_path, day_path, result_path, dashboard_dir=dashboard_dir)

    dashboard_path = dashboard_dir / "dashboard.html"
    assert dashboard_path.is_file()
    assert outcome["dashboard_path"] == str(dashboard_path)
    html = dashboard_path.read_text(encoding="utf-8")
    assert '"week_total":3' in html.replace(" ", "")


def test_generate_monitoring_report_defaults_dashboard_next_to_report(tmp_path):
    week_rows = _week_rows()
    day_rows = _day_rows()
    week_path = _write_window(tmp_path / "run" / "last_week", week_rows)
    day_path = _write_window(tmp_path / "run" / "last_day", day_rows)
    union_payload = _build_payload(week_rows)
    result = {
        "complete": True,
        "conversation_count": 3,
        "dataset_fingerprint": union_payload["dataset_fingerprint"],
        "categories": CATEGORIES,
        "classifications": _classifications(),
        "executive_summary": "Сводка",
        "recommendations": ["Рекомендация"],
        "period_analysis": {
            "week": {"conversation_count": 3, "dataset_fingerprint": union_payload["dataset_fingerprint"],
                     "executive_summary": "Неделя", "recommendations": ["..."]},
            "day": {"conversation_count": 1, "dataset_fingerprint": _build_payload(day_rows)["dataset_fingerprint"],
                    "executive_summary": "День", "recommendations": ["..."]},
        },
    }
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    outcome = generate_monitoring_report(week_path, day_path, result_path)

    expected = tmp_path / "run" / "support-analysis" / "dashboard.html"
    assert expected.is_file()
    assert outcome["dashboard_path"] == str(expected)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard.py -v -k monitoring_report`
Expected: FAIL — `TypeError: generate_monitoring_report() got an unexpected keyword argument 'dashboard_dir'`

- [ ] **Step 3: Write the implementation**

В `src/intercom_exporter/support_analysis.py` изменить сигнатуру и тело `generate_monitoring_report` (строки 569–635). Точные правки:

1. Сигнатура:

```python
def generate_monitoring_report(
    week_input_path: str | Path,
    day_input_path: str | Path,
    result_path: str | Path,
    output_dir: str | Path | None = None,
    dashboard_dir: str | Path | None = None,
) -> dict[str, Any]:
```

2. Вынести метки периодов в переменные (сейчас они инлайн в вызовах `_markdown_report`, строки 592–597). Заменить блок:

```python
    week_markdown, week_counts, _ = _markdown_report(
        week_rows, categories, week_classifications, period_analysis["week"], _period_label(week_path) or "Последние 7 дней",
    )
    day_markdown, day_counts, _ = _markdown_report(
        day_rows, categories, day_classifications, period_analysis["day"], _period_label(day_path) or "Последний день",
    )
```

на:

```python
    week_label = _period_label(week_path) or "Последние 7 дней"
    day_label = _period_label(day_path) or "Последний день"
    week_markdown, week_counts, _ = _markdown_report(
        week_rows, categories, week_classifications, period_analysis["week"], week_label,
    )
    day_markdown, day_counts, _ = _markdown_report(
        day_rows, categories, day_classifications, period_analysis["day"], day_label,
    )
```

3. После записи `classifications.csv` (строка 627) и перед `return` добавить генерацию дашборда и ключ в результат. Заменить блок:

```python
    csv_path = output / "classifications.csv"
    _write_classifications_csv(csv_path, classifications, {"week": week_ids, "day": day_ids})
    return {
        "unique_conversation_count": len(union_rows),
        "week_conversation_count": len(week_rows),
        "day_conversation_count": len(day_rows),
        "overlap_count": len(week_ids & day_ids),
        "report_path": str(report_path),
        "classifications_path": str(csv_path),
    }
```

на:

```python
    csv_path = output / "classifications.csv"
    _write_classifications_csv(csv_path, classifications, {"week": week_ids, "day": day_ids})
    from .dashboard import build_dashboard_data, write_dashboard

    dashboard_data = build_dashboard_data(week_rows, day_rows, categories, classifications, week_label, day_label)
    dashboard_path = write_dashboard(dashboard_data, Path(dashboard_dir) if dashboard_dir else output)
    return {
        "unique_conversation_count": len(union_rows),
        "week_conversation_count": len(week_rows),
        "day_conversation_count": len(day_rows),
        "overlap_count": len(week_ids & day_ids),
        "report_path": str(report_path),
        "classifications_path": str(csv_path),
        "dashboard_path": str(dashboard_path),
    }
```

(Импорт `dashboard` внутри функции — чтобы избежать циклического импорта: `dashboard.py` импортирует `_write_text` из `support_analysis`.)

4. В `build_parser()` после строки 660 (`monitor_report.add_argument("--output", ...)`) добавить:

```python
    monitor_report.add_argument("--dashboard-dir", help="Каталог для dashboard.html (по умолчанию: общий каталог отчёта)")
```

5. В `main()` заменить ветку `report-monitoring` (строки 730–732):

```python
        else:
            result = generate_monitoring_report(args.week, args.day, args.result, args.output)
            print(f"Единый отчёт сохранён: {result['report_path']}. Разметка: {result['classifications_path']}")
```

на:

```python
        else:
            result = generate_monitoring_report(args.week, args.day, args.result, args.output, args.dashboard_dir)
            print(
                f"Единый отчёт сохранён: {result['report_path']}. Разметка: {result['classifications_path']}. "
                f"Дашборд: {result['dashboard_path']}"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard.py -v`
Expected: 6 passed

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: весь набор зелёный (существующие тесты не сломаны)

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/support_analysis.py tests/test_dashboard.py
git commit -m "feat: report-monitoring generates dashboard.html (--dashboard-dir)"
```

---

### Task 4: Документация и прогон на реальных данных

**Files:**
- Modify: `README.md` (раздел мониторинга)

- [ ] **Step 1: Обновить README**

В раздел про `report-monitoring` README.md добавить абзац:

```markdown
Вместе с `report.md` и `classifications.csv` команда `report-monitoring` генерирует интерактивный офлайн-дашборд `dashboard.html` (KPI, график обращений по дням, круговые диаграммы категорий за неделю и день, топ-10 подкатегорий, распределение тяжести, таблица обращений высокой тяжести). Дашборд строится детерминированно из проверенных классификаций, без обращения к LLM. По умолчанию файл пишется в общий каталог анализа; каталог можно переопределить флагом `--dashboard-dir`:

```bash
python analyze_support.py report-monitoring --week .../last_week/conversations.json --day .../last_day/conversations.json --result .../chatgpt_result.json --dashboard-dir "D:\Program\My projects\Intercom\report_dashboard"
```
```

- [ ] **Step 2: Прогнать на реальных данных**

Run (из корня проекта):

```bash
.venv/Scripts/python.exe analyze_support.py report-monitoring --week "data/20261026T134214Z_20261102T134214Z/last_week/conversations.json" --day "data/20261026T134214Z_20261102T134214Z/last_day/conversations.json" --result "data/20261026T134214Z_20261102T134214Z/support-analysis/chatgpt_result.json" --dashboard-dir "D:\Program\My projects\Intercom\report_dashboard"
```

Expected: печать путей к report.md, classifications.csv и dashboard.html; код возврата 0.

- [ ] **Step 3: Проверить результат**

```bash
.venv/Scripts/python.exe -c "from pathlib import Path; html = Path(r'D:/Program/My projects/Intercom/report_dashboard/dashboard.html').read_text(encoding='utf-8'); print(len(html)); print('\"week_total\":851' in html.replace(' ', '')); print('Подключение VPN' in html)"
```

Expected: размер > 10 КБ, `True`, `True`. Открыть `dashboard.html` в браузере и визуально сверить: KPI = 851 / 162, топ-категория «Подключение VPN» 36 %, график за 27.10–02.11, два donut, бары, severity, таблица high.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: dashboard generation in report-monitoring"
```

---

## Self-Review

- **Spec coverage:** дашборд в `report_dashboard/` — Task 3 (`--dashboard-dir`) + Task 4 (прогон); график за неделю — `daily`; круговые диаграммы неделя+день — `week_categories`/`day_categories`; «и т.д.» — KPI, подкатегории, severity, high-кейсы; «в промт добавить или как правильно» — решение: детерминированный генератор, промпт не меняется (зафиксировано в Architecture).
- **Placeholders:** код полный во всех шагах; нет TBD.
- **Type consistency:** `build_dashboard_data(week_rows, day_rows, categories, classifications, week_period, day_period)` — одинаково в тестах Task 1/2/3 и в интеграции Task 3; `write_dashboard(data, output_dir) -> Path`; ключи `kpis.*`, `severity.week/day`, `daily[].date/count` совпадают между Python и JS (`DATA.kpis.week_total` и т.д.).

"""Детерминированная генерация интерактивного HTML-дашборда по результатам мониторинга."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
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
    <span class="badge" id="generated"></span>
    <span class="period" id="periods"></span>
  </header>
  <div class="kpis" id="kpis"></div>
  <div class="grid">
    <div class="card"><h2>Новые обращения по дням (неделя)</h2><div id="chart-daily"></div>
      <div style="font-size:12px;color:#6b7f88;margin-top:6px">Столбцы — целые даты, только новые обращения (по дате создания). Старые обращения с новой активностью здесь не считаются. KPI «день» — скользящие 24 часа от момента выгрузки, поэтому последний столбец может немного отличаться.</div>
    </div>
    <div class="card donut-card"><h2>Категории — всего за неделю</h2><div id="donut-week"></div><div class="legend" id="legend-week"></div></div>
    <div class="card donut-card"><h2>Категории — всего за сутки</h2><div id="donut-day"></div><div class="legend" id="legend-day"></div></div>
    <div class="card"><h2>Топ-10 подкатегорий (неделя)</h2><div id="bars-sub"></div></div>
    <div class="card"><h2>Тяжесть обращений</h2><div id="severity"></div></div>
  </div>
  <div class="card" style="margin-top:14px"><h2>Топ-10 обращений высокой тяжести (неделя)</h2><div id="high"></div></div>
  <div class="card" style="margin-top:14px"><h2>Рекомендации</h2><div id="recommendations"></div></div>
  <div class="card" style="margin-top:14px"><h2>Расшифровка категорий</h2><div id="categories"></div></div>
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
  ["Оплата и подписка (неделя)", DATA.kpis.billing_week, ""],
];
document.getElementById("kpis").innerHTML = kpis.map(([label, value, sub]) =>
  `<div class="kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="sub">${esc(sub)}</div></div>`).join("");

(function () {
  const el = document.getElementById("chart-daily");
  const rows = DATA.daily;
  if (!rows.length) { el.textContent = "Нет данных по датам"; return; }
  const W = 900, H = 280, PL = 56, PR = 24, PT = 24, PB = 48;
  const max = Math.max(...rows.map(r => r.count), 1);
  const raw = max / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), norm = raw / mag;
  const tickStep = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const yMax = tickStep * Math.ceil(max / tickStep);
  const x = i => PL + i * (W - PL - PR) / (rows.length - 1 || 1);
  const y = v => PT + (1 - v / yMax) * (H - PT - PB);
  const pts = rows.map((r, i) => `${x(i)},${y(r.count)}`).join(" ");
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  for (let v = 0; v <= yMax; v += tickStep) {
    svg += `<line x1="${PL}" y1="${y(v)}" x2="${W - PR}" y2="${y(v)}" stroke="${v ? "#e3ecef" : "#c3d3d9"}" stroke-width="1"/>` +
      `<text x="${PL - 10}" y="${y(v) + 5}" text-anchor="end" font-size="13" fill="#6b7f88">${v}</text>`;
  }
  svg += `<line x1="${PL}" y1="${PT}" x2="${PL}" y2="${H - PB}" stroke="#c3d3d9" stroke-width="1"/>` +
    `<text x="16" y="${PT + (H - PT - PB) / 2}" text-anchor="middle" font-size="13" fill="#6b7f88" transform="rotate(-90 16 ${PT + (H - PT - PB) / 2})">Обращений</text>` +
    `<polyline points="${pts}" fill="none" stroke="${PAL[0]}" stroke-width="2.5"/>`;
  rows.forEach((r, i) => {
    svg += `<circle cx="${x(i)}" cy="${y(r.count)}" r="4" fill="${PAL[1]}"/>` +
      `<text x="${x(i)}" y="${y(r.count) - 10}" text-anchor="middle" font-size="13">${r.count ? r.count : ""}</text>` +
      `<text x="${x(i)}" y="${H - PB + 20}" text-anchor="middle" font-size="13" fill="#6b7f88">${r.date.slice(8)}.${r.date.slice(5, 7)}</text>`;
  });
  svg += `<text x="${PL + (W - PL - PR) / 2}" y="${H - 6}" text-anchor="middle" font-size="13" fill="#6b7f88">Дата</text>`;
  el.innerHTML = svg + "</svg>";
})();

function donut(elId, legendId, rows) {
  const el = document.getElementById(elId), legend = document.getElementById(legendId);
  const total = rows.reduce((s, r) => s + r.count, 0);
  if (!total) { el.textContent = "Нет данных"; legend.textContent = ""; return; }
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
  const shown = DATA.high_cases.length;
  const total = DATA.high_cases_total || shown;
  const note = total > shown
    ? `<div style="font-size:12px;color:#6b7f88;margin-bottom:6px">Показано ${shown} из ${total} обращений высокой тяжести за неделю.</div>`
    : "";
  el.innerHTML = note + "<table><tr><th>ID</th><th>Категория</th><th>Подкатегория</th><th>Суть проблемы</th></tr>" +
    DATA.high_cases.map(c => `<tr><td>${esc(c.id)}</td><td>${esc(c.category)}</td><td>${esc(c.subcategory)}</td><td>${esc(c.problem_summary)}</td></tr>`).join("") + "</table>";
})();

(function () {
  const groups = [["overall", "Общие рекомендации"]];
  const el = document.getElementById("recommendations");
  const html = groups.map(([key, title]) => {
    const items = DATA.recommendations[key] || [];
    if (!items.length) return "";
    return `<h3 style="font-size:13px;margin:8px 0 4px">${title}</h3><ul style="padding-left:18px;font-size:13px">` +
      items.map(r => `<li style="margin:3px 0">${esc(r)}</li>`).join("") + "</ul>";
  }).join("");
  el.innerHTML = html || "Рекомендации не сформированы.";
})();

(function () {
  const el = document.getElementById("categories");
  if (!DATA.categories.length) { el.textContent = "Нет данных о категориях."; return; }
  el.innerHTML = "<table><tr><th>Категория</th><th>Описание</th></tr>" +
    DATA.categories.map(c => `<tr><td>${esc(c.name)}</td><td>${esc(c.description)}</td></tr>`).join("") + "</table>";
})();
</script>
</body>
</html>
"""


def render_dashboard_html(data: dict, prefix: str = "") -> str:
    """Возвращает самодостаточный офлайн HTML с внедрёнными данными (без внешних ресурсов).

    ``prefix`` добавляется ко всем id элементов и ссылкам getElementById —
    нужен для встраивания нескольких дашбордов на одну страницу без конфликтов.
    """
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = _TEMPLATE.replace("__DASHBOARD_DATA__", payload)
    if prefix:
        html = html.replace('id="', f'id="{prefix}')
        html = html.replace('document.getElementById("', f'document.getElementById("{prefix}')
    return html


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
.period{color:var(--muted);font-size:14px}
.tabs{display:flex;gap:8px;margin-bottom:12px;border-bottom:1px solid var(--border);padding-bottom:8px}
.tab{background:#fff;border:1px solid var(--border);border-radius:8px;padding:8px 16px;cursor:pointer;font-size:14px}
.tab.active{background:var(--teal);color:#fff;border-color:var(--teal)}
.tab-content{display:none}
.tab-content.active{display:block}
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
  <div class="tabs" id="tabs">__TABS__</div>
  __SECTIONS__
  <footer>Сгенерировано автоматически: analyze_support.py research</footer>
</div>
<script>
function switchTab(id){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active', t.dataset.tab===id));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.toggle('active', c.id===id));
}
document.getElementById('tabs').addEventListener('click', e=>{
  const tab = e.target.closest('.tab'); if(tab) switchTab(tab.dataset.tab);
});
</script>
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
    """Собирает общий дашборд с вкладками без iframe: каждый сервис
    рендерится в своём контейнере с префиксом id, поэтому страница
    работает в превью Slack и других ограниченных webview.

    ``services_html`` сохранён в сигнатуре для совместимости, но не используется.
    """
    stamp = datetime.now(timezone.utc).strftime("%d_%m_%Y")
    output_path = Path(output_dir) / f"dashboard_{stamp}.html"
    tabs = []
    sections = []
    first = True
    for sid in SERVICES:
        if sid not in services_data:
            continue
        label = SERVICES[sid].label
        active = " active" if first else ""
        tabs.append(f'<button class="tab{active}" data-tab="{sid}">{label}</button>')
        single = render_dashboard_html(services_data[sid], prefix=f"{sid}-")
        body = _extract_body_inner(single)
        sections.append(f'<div id="{sid}" class="tab-content{active}">{body}</div>')
        first = False
    html = _COMBINED_TEMPLATE.replace("__TABS__", "".join(tabs)) \
                           .replace("__SECTIONS__", "".join(sections)) \
                           .replace("__GENERATED_AT__", datetime.now(timezone.utc).isoformat(timespec="minutes"))
    _write_text(output_path, html)
    _write_text(output_path.with_suffix(".json"),
                json.dumps(services_data, ensure_ascii=False, indent=2))
    return output_path

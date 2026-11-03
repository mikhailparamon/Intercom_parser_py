import json

from intercom_exporter.dashboard import build_dashboard_data, render_dashboard_html, write_dashboard
from intercom_exporter.support_analysis import _build_payload, generate_monitoring_report

CATEGORIES = [
    {"name": "Подключение VPN", "description": "Проблемы с подключением"},
    {"name": "Оплата", "description": "Проблемы с оплатой"},
    {"name": "Оплата и управление подпиской", "description": "Оплата и подписка"},
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
    assert data["kpis"]["billing_week"] == 0
    assert data["week_categories"][0] == {"name": "Подключение VPN", "count": 2, "share": 66.7}
    assert data["day_categories"] == [{"name": "Подключение VPN", "count": 1, "share": 100.0}]
    assert data["severity"]["week"] == {"high": 1, "medium": 1, "low": 0, "unknown": 1}
    assert data["severity"]["day"] == {"high": 0, "medium": 0, "low": 0, "unknown": 1}
    assert data["subcategories"][0] == {"name": "Не подключается", "count": 2}
    assert [case["id"] for case in data["high_cases"]] == ["w1"]
    assert data["week"]["period_label"] == "week label"
    assert data["day"]["period_label"] == "day label"


def test_daily_series_covers_seven_days_ending_at_latest_activity():
    rows = [
        {"id": "w1", "created_at": "2026-10-26T09:00:00Z", "updated_at": "2026-10-26T10:00:00Z",
         "conversation_parts": {"conversation_parts": [{"part_type": "comment", "created_at": "2026-11-01T12:00:00Z"}]}},
        {"id": "w2", "created_at": "2026-10-27T00:00:00Z", "updated_at": "2026-11-01T12:00:00Z",
         "conversation_parts": {"conversation_parts": [{"part_type": "comment", "created_at": "2026-11-01T13:00:00Z"}]}},
        {"id": "d1", "created_at": "2026-10-28T00:00:00Z", "updated_at": "2026-11-02T08:00:00Z",
         "conversation_parts": {"conversation_parts": [{"part_type": "open", "created_at": "2026-11-02T08:00:00Z"}]}},
    ]
    data = build_dashboard_data(rows, rows, CATEGORIES, _classifications(), "w", "d")
    assert [item["date"] for item in data["daily"]] == [
        "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02",
    ]
    assert [item["count"] for item in data["daily"]] == [0, 0, 0, 0, 0, 2, 1]


def test_billing_week_counts_billing_category():
    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, [
        _classification("w1", "Оплата и управление подпиской", "Отмена подписки", None),
        _classification("w2", "Оплата", "Карта отклонена", None),
        _classification("d1", "Оплата и управление подпиской", "Возврат денег", None),
    ], "w", "d")
    assert data["kpis"]["billing_week"] == 2


def test_daily_series_empty_when_no_rows():
    data = build_dashboard_data([], [], CATEGORIES, [], "w", "d")
    assert data["daily"] == []
    assert data["kpis"]["week_total"] == 0
    assert data["kpis"]["top_category"] == "—"


def test_daily_series_falls_back_to_created_at_when_updated_at_unparseable():
    rows = [
        {"id": "a", "created_at": "2026-11-01T10:00:00Z", "updated_at": "not-a-date"},
        {"id": "b", "created_at": "2026-11-02T09:00:00Z"},
    ]
    data = build_dashboard_data(rows, [], CATEGORIES, [
        _classification("a", "Оплата", "s", None),
        _classification("b", "Оплата", "s", None),
    ], "w", "d")
    assert data["daily"][-1] == {"date": "2026-11-02", "count": 1}
    assert data["daily"][-2] == {"date": "2026-11-01", "count": 1}


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



def _write_window(directory, rows):
    directory.mkdir(parents=True)
    (directory / "conversations.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    (directory / "manifest.json").write_text(
        json.dumps({"start": "2026-10-26", "end_exclusive": "2026-11-02"}), encoding="utf-8")
    return directory / "conversations.json"


def _monitoring_result(week_rows, day_rows):
    union_payload = _build_payload(week_rows)  # day_rows ⊂ week_rows по id
    return {
        "complete": True,
        "conversation_count": len(week_rows),
        "dataset_fingerprint": union_payload["dataset_fingerprint"],
        "categories": CATEGORIES,
        "classifications": _classifications(),
        "executive_summary": "Сводка",
        "recommendations": ["Рекомендация"],
        "period_analysis": {
            "week": {"conversation_count": len(week_rows), "dataset_fingerprint": union_payload["dataset_fingerprint"],
                     "executive_summary": "Неделя", "recommendations": ["..."]},
            "day": {"conversation_count": len(day_rows), "dataset_fingerprint": _build_payload(day_rows)["dataset_fingerprint"],
                    "executive_summary": "День", "recommendations": ["..."]},
        },
    }


def test_generate_monitoring_report_writes_dashboard(tmp_path):
    week_rows = _week_rows()
    day_rows = _day_rows()
    week_path = _write_window(tmp_path / "run" / "last_week", week_rows)
    day_path = _write_window(tmp_path / "run" / "last_day", day_rows)
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_monitoring_result(week_rows, day_rows), ensure_ascii=False), encoding="utf-8")
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
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_monitoring_result(week_rows, day_rows), ensure_ascii=False), encoding="utf-8")

    outcome = generate_monitoring_report(week_path, day_path, result_path)

    expected = tmp_path / "run" / "support-analysis" / "dashboard.html"
    assert expected.is_file()
    assert outcome["dashboard_path"] == str(expected)


def test_build_dashboard_data_includes_categories_and_recommendations():
    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, _classifications(), "w", "d",
                                {"overall": ["O1"], "week": ["W1", "W2"], "day": []})
    assert data["categories"] == [{"name": c["name"], "description": c["description"]} for c in CATEGORIES]
    assert data["recommendations"] == {"overall": ["O1"], "week": ["W1", "W2"], "day": []}


def test_build_dashboard_data_defaults_empty_recommendations():
    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, _classifications(), "w", "d")
    assert data["recommendations"] == {"overall": [], "week": [], "day": []}


def test_high_cases_capped_at_top_ten_with_total():
    rows = [{"id": f"h{i}", "created_at": "2026-10-26T09:00:00Z", "updated_at": "2026-10-26T10:00:00Z"}
            for i in range(12)]
    classifications = [_classification(f"h{i}", "Подключение VPN", "Не подключается", "high") for i in range(12)]

    data = build_dashboard_data(rows, rows, CATEGORIES, classifications, "w", "d")

    assert len(data["high_cases"]) == 10
    assert data["high_cases_total"] == 12
    assert [case["id"] for case in data["high_cases"]] == [f"h{i}" for i in range(10)]


def test_daily_series_counts_last_meaningful_activity_day_not_updated_at():
    def row(cid, created, updated, parts):
        return {"id": cid, "created_at": created, "updated_at": updated,
                "conversation_parts": {"conversation_parts": parts}}

    rows = [
        # старое обращение, реальное сообщение 30 октября, но updated_at = bulk_reassignment 2 ноября
        row("a", "2026-07-08T10:00:00Z", "2026-11-02T15:00:00Z",
            [{"part_type": "comment", "created_at": "2026-10-30T12:00:00Z"},
             {"part_type": "bulk_reassignment", "created_at": "2026-11-02T15:00:00Z"}]),
        # сообщение 2 ноября
        row("b", "2026-11-02T09:00:00Z", "2026-11-02T10:00:00Z",
            [{"part_type": "open", "created_at": "2026-11-02T09:00:00Z"}]),
        # только системное событие в окне — считается по созданию
        row("c", "2026-11-01T09:00:00Z", "2026-11-02T15:00:00Z",
            [{"part_type": "bulk_reassignment", "created_at": "2026-11-02T15:00:00Z"}]),
    ]

    data = build_dashboard_data(rows, rows, CATEGORIES, [], "w", "d")
    series = {item["date"]: item["count"] for item in data["daily"]}

    assert series["2026-10-30"] == 1  # обращение «а» — по дню реального сообщения
    assert series["2026-11-01"] == 1  # обращение «c» — по созданию
    assert series["2026-11-02"] == 1  # обращение «b» — не 3 (updated_at не используется)

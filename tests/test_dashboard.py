from intercom_exporter.dashboard import build_dashboard_data, render_dashboard_html, write_dashboard

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


def test_cancel_week_counts_categories_with_cancellation_in_name():
    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, [
        _classification("w1", "Подписка и отмена", "Хочет отменить", None),
        _classification("w2", "Оплата", "Карта отклонена", None),
        _classification("d1", "Подписка и отмена", "Возврат денег", None),
    ], "w", "d")
    assert data["kpis"]["cancel_week"] == 2


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

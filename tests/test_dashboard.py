import json

from intercom_exporter.dashboard import build_dashboard_data, render_dashboard_html, write_combined_dashboard, write_dashboard
from intercom_exporter.support_analysis import _build_payload, generate_monitoring_report

CATEGORIES = [
    {"name": "Подключение VPN", "description": "Проблемы с подключением"},
    {"name": "Оплата", "description": "Проблемы с оплатой"},
    {"name": "Оплата и подписка", "description": "Оплата и подписка"},
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


def test_rankings_report_unique_coverage_and_keep_platforms_in_one_card():
    rows = [{"id": str(i)} for i in range(13)]
    items = [_classification(str(i), "Оплата", f"Подкатегория {i}", None) for i in range(13)]
    for i, item in enumerate(items):
        item["subcategory_code"] = f"code_{i}"
        item["platforms"] = ["OpenVPN", "Windows"] if i < 8 else []
    items[0]["platforms"] += ["OpenVPN", "Приложение VPN Наоборот"]
    data = build_dashboard_data(rows, [], CATEGORIES, items, "week", "day")
    assert data["subcategory_coverage"] == {"total": 13, "in_top": 10, "outside_top": 3}
    assert len(data["all_subcategories"]) == 13
    assert data["platform_coverage"] == {"total": 13, "with_platform": 8, "without_platform": 5, "in_top": 8, "outside_top": 0, "multiple": 8}
    assert {p["name"]: p["count"] for p in data["platforms"]} == {"OpenVPN": 8, "Windows": 8, "Приложение VPN Наоборот": 1}
    html = render_dashboard_html(data)
    assert html.count("<h2>Платформы (неделя)</h2>") == 1
    assert "Одно обращение может учитываться в нескольких платформах" in html
    assert "Платформа определена в 8 из 13" in html
    assert "Вне топ-10: 3" in html


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
    assert {"name": "Не подключается", "count": 2} in data["subcategories"]
    assert [case["id"] for case in data["high_cases"]] == ["w1"]
    assert data["week"]["period_label"] == "week label"
    assert data["day"]["period_label"] == "day label"


def test_daily_series_covers_seven_days_ending_at_latest_created():
    rows = [
        {"id": "w1", "created_at": "2026-10-26T09:00:00Z", "updated_at": "2026-11-01T12:00:00Z"},
        {"id": "w2", "created_at": "2026-10-27T00:00:00Z", "updated_at": "2026-11-01T13:00:00Z"},
        {"id": "d1", "created_at": "2026-10-28T00:00:00Z", "updated_at": "2026-11-02T08:00:00Z"},
    ]
    data = build_dashboard_data(rows, rows, CATEGORIES, _classifications(), "w", "d")
    assert [item["date"] for item in data["daily"]] == [
        "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26", "2026-10-27", "2026-10-28",
    ]
    assert [item["count"] for item in data["daily"]] == [0, 0, 0, 0, 1, 1, 1]



def test_billing_week_counts_billing_category():
    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, [
        _classification("w1", "Оплата и подписка", "Отмена подписки", None),
        _classification("w2", "Оплата", "Карта отклонена", None),
        _classification("d1", "Оплата и подписка", "Возврат денег", None),
    ], "w", "d")
    assert data["kpis"]["billing_week"] == 2


def test_docs_tariff_replaces_no_request_in_subcategory_ranking_without_separate_card():
    rows = _week_rows()
    classifications = [
        _classification("w1", "Совместимость и ограничения", "Ограничение тарифа «Для документов»", None),
        _classification("w2", "Прочие обращения", "Нет запроса", None),
        _classification("d1", "Оплата и подписка", "Смена тарифа", None),
    ]
    classifications[2]["additional_topics"] = ["Ограничение тарифа «Для документов»"]

    data = build_dashboard_data(rows, _day_rows(), CATEGORIES, classifications, "w", "d")
    html = render_dashboard_html(data)

    assert data["docs_tariff"]["week_count"] == 2
    assert data["docs_tariff"]["day_count"] == 1
    assert {item["name"]: item["count"] for item in data["subcategories"]} == {
        'Тариф «Для документов»': 2, 'Смена тарифа': 1,
    }
    assert '<span>Тариф «Для документов»</span>' in html
    assert '<h2>Ограничение тарифа «Для документов»</h2>' not in html


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
    assert path.parent == tmp_path
    assert path.name.startswith("dashboard_") and path.name.endswith(".html")
    html = path.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "Подключение VPN" in html
    assert "Обращений за неделю" in html
    assert "http" not in html.split("<body")[0].replace("https://", "").replace("http://", "")  # нет CDN в <head>
    assert "<script" not in html  # страница статичная: работает и без JavaScript


def test_write_dashboard_names_file_by_generation_date(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import intercom_exporter.dashboard as dashboard_module

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 3, 15, 40, tzinfo=timezone.utc)

    monkeypatch.setattr(dashboard_module, "datetime", _FixedDatetime)
    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, _classifications(), "w", "d")

    path = write_dashboard(data, tmp_path)

    assert path == tmp_path / "dashboard_03_11_2026.html"


def test_write_dashboard_writes_data_json_next_to_html(tmp_path):
    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, _classifications(), "w", "d")

    path = write_dashboard(data, tmp_path)

    json_path = path.with_suffix(".json")
    assert json_path.is_file()
    assert json.loads(json_path.read_text(encoding="utf-8"))["kpis"]["week_total"] == 3


def test_render_dashboard_html_escapes_script_closing_tag():
    categories = [{"name": "X</script>", "description": "d"}]
    rows = [{"id": "a", "updated_at": "2026-11-02T00:00:00Z"}]
    classifications = [_classification("a", "X</script>", "sub", None)]
    html = render_dashboard_html(build_dashboard_data(rows, rows, categories, classifications, "w", "d"))
    assert "X</script>" not in html.replace("</style>", "").replace("</script>", "")
    assert "X&lt;/script&gt;" in html


def test_write_combined_dashboard_creates_static_html_and_json(tmp_path):
    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, _classifications(), "w", "d")
    single_path = write_dashboard(data, tmp_path / "vpn")
    services_data = {"vpn": data}
    services_html = {"vpn": single_path.read_text(encoding="utf-8")}

    path = write_combined_dashboard(services_data, services_html, tmp_path)

    assert path.name.startswith("dashboard_") and path.name.endswith(".html")
    html = path.read_text(encoding="utf-8")
    assert "VPNНаоборот" in html
    assert "data:text/html;base64," not in html
    assert "<iframe" not in html
    assert "<script" not in html  # общий дашборд статичный: работает в превью Slack
    assert 'href="#vpn"' in html
    json_path = path.with_suffix(".json")
    assert json_path.is_file()
    assert json.loads(json_path.read_text(encoding="utf-8"))["vpn"]["kpis"]["week_total"] == 3



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

    dashboards = list(dashboard_dir.glob("dashboard_*.html"))
    assert len(dashboards) == 1
    dashboard_path = dashboards[0]
    assert outcome["dashboard_path"] == str(dashboard_path)
    html = dashboard_path.read_text(encoding="utf-8")
    assert "Обращений за неделю" in html
    assert '<div class="value">3</div>' in html


def test_generate_monitoring_report_defaults_dashboard_next_to_report(tmp_path):
    week_rows = _week_rows()
    day_rows = _day_rows()
    week_path = _write_window(tmp_path / "run" / "last_week", week_rows)
    day_path = _write_window(tmp_path / "run" / "last_day", day_rows)
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_monitoring_result(week_rows, day_rows), ensure_ascii=False), encoding="utf-8")

    outcome = generate_monitoring_report(week_path, day_path, result_path)

    dashboards = list((tmp_path / "run" / "support-analysis").glob("dashboard_*.html"))
    assert len(dashboards) == 1
    assert outcome["dashboard_path"] == str(dashboards[0])


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


def test_daily_series_counts_only_new_conversations_by_created_at():
    def row(cid, created, updated, parts):
        return {"id": cid, "created_at": created, "updated_at": updated,
                "conversation_parts": {"conversation_parts": parts}}

    rows = [
        # старое обращение (июль), реальное сообщение 30 октября — НЕ новое, на график не попадает
        row("a", "2026-07-08T10:00:00Z", "2026-11-02T15:00:00Z",
            [{"part_type": "comment", "created_at": "2026-10-30T12:00:00Z"}]),
        # создано 2 ноября — попадает в 02.11
        row("b", "2026-11-02T09:00:00Z", "2026-11-02T10:00:00Z",
            [{"part_type": "open", "created_at": "2026-11-02T09:00:00Z"}]),
        # создано 1 ноября, updated_at = bulk_reassignment — попадает в 01.11
        row("c", "2026-11-01T09:00:00Z", "2026-11-02T15:00:00Z",
            [{"part_type": "bulk_reassignment", "created_at": "2026-11-02T15:00:00Z"}]),
    ]

    data = build_dashboard_data(rows, rows, CATEGORIES, [], "w", "d")
    series = {item["date"]: item["count"] for item in data["daily"]}

    assert series["2026-10-30"] == 0  # обращение «а» создано в июле — не новое
    assert series["2026-11-01"] == 1  # обращение «c»
    assert series["2026-11-02"] == 1  # обращение «b» — не 2 (updated_at не используется)


def test_high_cases_list_day_window_first():
    week_rows = [
        {"id": "w_old", "created_at": "2026-10-26T09:00:00Z", "updated_at": "2026-10-26T10:00:00Z"},
        {"id": "d_new", "created_at": "2026-11-02T09:00:00Z", "updated_at": "2026-11-02T10:00:00Z"},
    ]
    day_rows = [week_rows[1]]
    classifications = [
        _classification("w_old", "Подключение VPN", "Не подключается", "high"),
        _classification("d_new", "Подключение VPN", "Не подключается", "high"),
    ]

    data = build_dashboard_data(week_rows, day_rows, CATEGORIES, classifications, "w", "d")

    assert [case["id"] for case in data["high_cases"]] == ["d_new", "w_old"]


def test_high_cases_day_contains_only_day_window_high_severity():
    week_rows = [
        {"id": "w_old", "created_at": "2026-10-26T09:00:00Z", "updated_at": "2026-10-26T10:00:00Z"},
        {"id": "d_high", "created_at": "2026-11-02T09:00:00Z", "updated_at": "2026-11-02T10:00:00Z"},
        {"id": "d_low", "created_at": "2026-11-02T11:00:00Z", "updated_at": "2026-11-02T12:00:00Z"},
    ]
    day_rows = week_rows[1:]
    classifications = [
        _classification("w_old", "Подключение VPN", "Не подключается", "high"),
        _classification("d_high", "Подключение VPN", "Не подключается", "high"),
        _classification("d_low", "Подключение VPN", "Не подключается", "low"),
    ]

    data = build_dashboard_data(week_rows, day_rows, CATEGORIES, classifications, "w", "d")

    assert [case["id"] for case in data["high_cases_day"]] == ["d_high"]


def test_subcategories_are_not_forced_to_include_platforms():
    classifications = [
        _classification("w1", "Подключение VPN", "Не подключается", None),
    ]

    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, classifications, "w", "d")

    names = [item["name"] for item in data["subcategories"]]
    assert {"name": "Не подключается", "count": 1} in data["subcategories"]
    assert len(names) == 1


def test_subcategories_fill_remaining_slots_with_free():
    rows = [{"id": f"c{i}", "created_at": "2026-10-26T09:00:00Z", "updated_at": "2026-10-26T10:00:00Z"}
            for i in range(30)]
    classifications = (
        [_classification(f"c{i}", "Подключение VPN", "Не подключается", None) for i in range(5)]
        + [_classification(f"c{i}", "Оплата", "Карта отклонена", None) for i in range(5, 10)]
        + [_classification(f"c{i}", "Оплата", "Медленный возврат", None) for i in range(10, 15)]
        + [_classification(f"c{i}", "Оплата", "Сбой списания", None) for i in range(15, 20)]
        + [_classification(f"c{i}", "Оплата", "Возврат", None) for i in range(20, 25)]
        + [_classification(f"c{i}", "Оплата", "Редкая свободная", None) for i in range(25, 30)]
    )

    data = build_dashboard_data(rows, rows, CATEGORIES, classifications, "w", "d")

    names = [item["name"] for item in data["subcategories"]]
    assert len(names) == 6
    assert "Карта отклонена" in names
    assert "Редкая свободная" in names


def test_subcategories_sorted_by_count_desc():
    rows = [{"id": f"c{i}", "created_at": "2026-10-26T09:00:00Z", "updated_at": "2026-10-26T10:00:00Z"}
            for i in range(4)]
    classifications = [
        _classification("c0", "Подключение VPN", "Возврат", None),   # 1
        _classification("c1", "Подключение VPN", "Сброс соединения", None),  # 1
        _classification("c2", "Подключение VPN", "Популярная свободная", None),
        _classification("c3", "Подключение VPN", "Популярная свободная", None),
    ]

    data = build_dashboard_data(rows, rows, CATEGORIES, classifications, "w", "d")

    counts = [item["count"] for item in data["subcategories"]]
    assert counts == sorted(counts, reverse=True)
    names = [item["name"] for item in data["subcategories"]]
    assert "Популярная свободная" in names
def test_platform_counts_are_separate_from_subcategory_ranking():
    rows = _week_rows()
    classifications = [
        {**_classification("w1", "Подключение VPN", "Код не приходит", None), "platforms": ["iOS", "Chrome"]},
        {**_classification("w2", "Оплата", "Возврат", None), "platforms": ["TV/приставка"]},
    ]
    data = build_dashboard_data(rows, _day_rows(), CATEGORIES, classifications, "w", "d")
    assert {item["name"] for item in data["subcategories"]} == {"Код не приходит", "Возврат"}
    assert {item["name"] for item in data["platforms"]} == {"Расширение Chrome", "TV/приставка", "iOS"}


def test_dashboard_omits_non_informative_platform_labels():
    rows = _week_rows()
    classifications = [
        {**_classification("w1", "Оплата", "Вопрос", None),
         "platforms": ["Телефон", "App Store", "Египет", "Разные устройства", "Linux", "Google TV",
                       "Приложение VPN Наоборот", "Расширение другого браузера", "Роутер"]},
    ]

    data = build_dashboard_data(rows, _day_rows(), CATEGORIES, classifications, "w", "d")

    assert data["platforms"] == []
    assert "Телефон" not in render_dashboard_html(data)
    assert "App Store" not in render_dashboard_html(data)
    assert "Египет" not in render_dashboard_html(data)
    assert "Разные устройства" not in render_dashboard_html(data)


def test_dashboard_limits_platforms_to_top_ten():
    rows = [{"id": str(i), "created_at": "2026-11-01T00:00:00Z"} for i in range(11)]
    classifications = [
        {**_classification(str(i), "Категория", "Проблема", None), "platforms": [f"Платформа {i}"]}
        for i in range(11)
    ]

    data = build_dashboard_data(rows, [], CATEGORIES, classifications, "w", "d")

    assert len(data["platforms"]) == 10
    assert [item["name"] for item in data["platforms"]] == sorted([f"Платформа {i}" for i in range(11)], key=str.casefold)[:10]
    assert "Платформы" in render_dashboard_html(data)

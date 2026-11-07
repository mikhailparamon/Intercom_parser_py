import json
from pathlib import Path

import pytest

from intercom_exporter.agents_orchestration import (
    TAXONOMY,
    _apply_fixed_subcategory_rules,
    _apply_hypertube_fixed_subcategory_rules,
    _conversation_text,
    _normalize_hypertube_subcategory,
    _normalize_subcategory,
    _normalized_classification,
    merge_agents_results,
    prepare_agents_bundle,
)
from intercom_exporter.support_analysis import AnalysisError


def _write_conversations(directory, ids):
    directory.mkdir(parents=True)
    rows = [{"id": cid, "created_at": "2026-11-01T10:00:00Z", "updated_at": "2026-11-01T10:00:00Z",
             "source": {"body": f"Клиент {cid}: не работает VPN"},
             "conversation_parts": {"conversation_parts": []}} for cid in ids]
    path = directory / "conversations.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def test_prepare_agents_bundle_writes_chunks_manifest_and_protocol(tmp_path):
    week = _write_conversations(tmp_path / "last_week", [f"w{i}" for i in range(25)])
    day = _write_conversations(tmp_path / "last_day", ["w0", "w1", "w2"])
    out = tmp_path / "support-analysis"

    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=5)

    work = out / "agents_work"
    assert (work / "taxonomy.json").is_file()
    assert (work / "manifest.json").is_file()
    assert (work / "agents_protocol.md").is_file()
    protocol = (work / "agents_protocol.md").read_text(encoding="utf-8")
    prompt = (out / "PROMPT.md").read_text(encoding="utf-8")
    assert "platforms" in protocol and "additional_topics" in protocol
    assert "Подкатегория описывает" in prompt
    assert "Оплата сама по себе не означает неактивную подписку" in prompt
    taxonomy = json.loads((work / "taxonomy.json").read_text(encoding="utf-8"))["categories"]
    assert [item["name"] for item in taxonomy] == [item["name"] for item in TAXONOMY]
    assert len(taxonomy) == 10
    chunk_files = sorted(work.glob("chunk_*.json"))
    assert len(chunk_files) == 5
    total = sum(len(json.loads(f.read_text(encoding="utf-8"))["conversations"]) for f in chunk_files)
    assert total == 25  # объединение week ∪ day без дублей
    assert manifest["workers"] == 5
    assert manifest["unique_conversation_count"] == 25
    assert manifest["taxonomy_source"] == "static_v5"
    assert len(manifest["chunks"]) == 5
    for entry in manifest["chunks"]:
        assert Path(entry["result_path"]).name.startswith("result_")
        assert entry["conversation_count"] > 0


def test_hypertube_prompt_keeps_problem_and_platform_separate(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    prepare_agents_bundle(week, day, out, schema_version=1, workers=2, service_id="hypertube")
    prompt = (out / "PROMPT.md").read_text(encoding="utf-8")
    assert "На телевизоре бесконечно грузится видео" in prompt
    assert "Нет подключения к интернету" in prompt
    assert "additional_topics" in prompt
    assert "YouTube не работает" in prompt
    assert "Сначала уточни симптом по тексту клиента" in prompt


def test_hypertube_resolves_generic_subcategory_from_explicit_customer_symptom():
    from intercom_exporter.agents_orchestration import _refine_hypertube_unspecified_subcategory

    conv = {"title": "", "messages": [{"role": "customer", "text": "Снова не работает Ютуб"}]}
    assert _refine_hypertube_unspecified_subcategory(
        "Проблема не уточнена", "Доступ к YouTube", conv
    ) == "YouTube не работает"


def test_hypertube_does_not_infer_symptom_from_operator_template_or_vague_text():
    from intercom_exporter.agents_orchestration import _refine_hypertube_unspecified_subcategory

    operator_only = {"title": "", "messages": [{"role": "operator", "text": "На каком устройстве не работает Ютуб?"}]}
    vague = {"title": "", "messages": [{"role": "customer", "text": "Не работает"}]}
    empty = {"title": "", "messages": []}
    assert _refine_hypertube_unspecified_subcategory(
        "Проблема не уточнена", "Доступ к YouTube", operator_only
    ) == "Проблема не уточнена"
    assert _refine_hypertube_unspecified_subcategory(
        "Проблема не уточнена", "Доступ к YouTube", vague
    ) == "YouTube не работает"
    assert _refine_hypertube_unspecified_subcategory(
        "Проблема не уточнена", "Доступ к YouTube", empty
    ) == "Проблема не уточнена"


@pytest.mark.parametrize(
    ("category", "text", "expected"),
    [
        ("Расширение браузера", "Снова не работает расширение", "Расширение не работает"),
        ("Приложение", "Приложение зависает", "Приложение зависает"),
        ("Доступ к YouTube", "Вчера и сегодня не работает ускорение", "Ускорение не работает"),
        ("Доступ к YouTube", "Видео не загружается", "Видео не загружается"),
        ("Доступ к YouTube", "Пишет: нет подключения к интернету", "Нет подключения к интернету"),
        ("Доступ к YouTube", "Не могу оплатить, сайт недоступен, с интернетом всё нормально; YouTube не работает", "YouTube не работает"),
        ("Авторизация и аккаунт", "Мне не пришёл код", "Код не приходит"),
        ("Расширение браузера", "Не работает на компьютере", "Расширение не работает"),
        ("Приложение", "Здравствуйте, не работает", "Приложение не работает"),
        ("Приложение", "А почему нету кнопки подключить?", "Кнопка подключения отсутствует"),
        ("Расширение браузера", "Плохо работает расширение оплаченное", "Расширение работает нестабильно"),
        ("Расширение браузера", "На расширении YouTube перестал открываться", "Расширение не работает"),
        ("Оплата и подписка", "Как можно продлить подписку?", "Продление подписки"),
        ("Оплата и подписка", "Оплатил, но ничего не работает", "Проблема после оплаты"),
        ("Установка и настройка", "Здравствуйте, не работает впн", "VPN не работает"),
        ("Авторизация и аккаунт", "Не работает. Используйте код для входа в аккаунт", "Ошибка входа"),
        ("Расширение браузера", "Спасибо, видимо AdBlock блочил", "Конфликт с блокировщиком рекламы"),
        ("Установка и настройка", "Обычная переустановка?", "Переустановка приложения или расширения"),
    ],
)
def test_hypertube_refines_explicit_symptoms(category, text, expected):
    from intercom_exporter.agents_orchestration import _refine_hypertube_unspecified_subcategory

    conv = {"title": "", "messages": [{"role": "customer", "text": text}]}
    assert _refine_hypertube_unspecified_subcategory(
        "Проблема не уточнена", category, conv
    ) == expected


def test_hypertube_uses_customer_confirmation_after_operator_context():
    from intercom_exporter.agents_orchestration import _refine_hypertube_unspecified_subcategory

    conv = {"title": "", "messages": [
        {"role": "operator", "text": "На каком устройстве не работает Ютуб?"},
        {"role": "customer", "text": "На компьютере"},
    ]}
    assert _refine_hypertube_unspecified_subcategory(
        "Проблема не уточнена", "Доступ к YouTube", conv
    ) == "YouTube не работает"


def _fill_results(manifest, category="Проблемы подключения VPN", skip_worker=None):
    for entry in manifest["chunks"]:
        if entry["worker"] == skip_worker:
            continue
        chunk = json.loads(Path(entry["chunk_path"]).read_text(encoding="utf-8"))
        items = [{
            "id": conv["id"], "category": "Проблемы подключения VPN" if category is None else category,
            "subcategory": "Не подключается", "problem_summary": f"Суть {conv['id']}",
            "severity": None, "severity_reason": "влияние не описано",
            "confidence": "high", "evidence": "не работает VPN",
        } for conv in chunk["conversations"]]
        Path(entry["result_path"]).write_text(json.dumps({"classifications": items}, ensure_ascii=False), encoding="utf-8")


def test_merge_agents_results_validates_and_builds_report(tmp_path):
    week = _write_conversations(tmp_path / "last_week", [f"w{i}" for i in range(6)])
    day = _write_conversations(tmp_path / "last_day", ["w0", "w1"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=3)
    _fill_results(manifest, category=None)

    outcome = merge_agents_results(week, day, out)

    assert outcome["unique_conversation_count"] == 6
    result = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    assert result["complete"] is True
    assert len(result["classifications"]) == 6
    assert [c["name"] for c in result["categories"]] == [c["name"] for c in TAXONOMY]
    assert Path(outcome["report_path"]).is_file()
    assert Path(outcome["dashboard_path"]).is_file()


def test_merge_agents_results_reports_missing_worker(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None, skip_worker=2)

    with pytest.raises(AnalysisError, match="воркер 2"):
        merge_agents_results(week, day, out)


def test_merge_agents_results_rejects_unknown_category(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category="Выдуманная категория")

    with pytest.raises(AnalysisError, match="не из таксономии"):
        merge_agents_results(week, day, out)


def test_merge_agents_results_compare_with_previous(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)
    previous = {"classifications": [
        {"id": "a", "category": "Проблемы подключения VPN", "subcategory": "Не подключается", "severity": None},
        {"id": "b", "category": "Ключи и конфигурации", "subcategory": "s", "severity": None},
    ]}
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")

    outcome = merge_agents_results(week, day, out, compare_with=previous_path)

    comparison = Path(outcome["comparison_path"]).read_text(encoding="utf-8")
    assert "50.0%" in comparison  # категория совпала у 1 из 2
    assert outcome["comparison_path"].endswith("comparison_report.md")


def test_merge_agents_results_rejects_unknown_id(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)
    entry = manifest["chunks"][0]
    result = json.loads(Path(entry["result_path"]).read_text(encoding="utf-8"))
    result["classifications"].append({
        "id": "ghost", "category": "Проблемы подключения VPN", "subcategory": "s",
        "problem_summary": "s", "severity": None, "severity_reason": "—",
        "confidence": "high", "evidence": "",
    })
    Path(entry["result_path"]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AnalysisError, match="неизвестный ID"):
        merge_agents_results(week, day, out)


def test_merge_agents_results_rejects_non_list_classifications(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)
    entry = manifest["chunks"][0]
    Path(entry["result_path"]).write_text(json.dumps({"classifications": {"a": "x"}}, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AnalysisError, match="не является списком"):
        merge_agents_results(week, day, out)


def test_merge_agents_results_rejects_non_dict_item(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)
    entry = manifest["chunks"][0]
    Path(entry["result_path"]).write_text(
        json.dumps({"classifications": ["не словарь"]}, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AnalysisError, match="некорректная запись классификации"):
        merge_agents_results(week, day, out)


def test_prepare_agents_bundle_caps_workers_at_conversation_count(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b", "c"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"

    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=20)

    assert manifest["workers"] == 3
    assert len(manifest["chunks"]) == 3


def test_prepare_agents_bundle_generates_prompt(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"

    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)

    prompt_path = out / "PROMPT.md"
    assert prompt_path.is_file()
    assert manifest["prompt_path"] == str(prompt_path)
    assert "таксоном" in prompt_path.read_text(encoding="utf-8").lower()


def test_cli_prepare_monitoring_creates_agents_bundle_by_default(tmp_path, capsys):
    from intercom_exporter.support_analysis import main

    week = _write_conversations(tmp_path / "run" / "last_week", ["a", "b", "c"])
    day = _write_conversations(tmp_path / "run" / "last_day", ["a"])
    code = main(["prepare-monitoring", "--week", str(week), "--day", str(day)])
    assert code == 0
    bundle = tmp_path / "run" / "support-analysis" / "agents_work"
    assert (bundle / "manifest.json").is_file()
    assert (bundle / "agents_protocol.md").is_file()
    assert "оркестрац" in capsys.readouterr().out.lower()


def test_cli_prepare_monitoring_agents_disabled_with_flag(tmp_path):
    from intercom_exporter.support_analysis import main

    week = _write_conversations(tmp_path / "run" / "last_week", ["a"])
    day = _write_conversations(tmp_path / "run" / "last_day", ["a"])
    code = main(["prepare-monitoring", "--week", str(week), "--day", str(day), "--no-agents"])
    assert code == 0
    assert not (tmp_path / "run" / "support-analysis" / "agents_work").exists()


def test_cli_merge_agents(tmp_path, capsys):
    from intercom_exporter.support_analysis import main

    week = _write_conversations(tmp_path / "run" / "last_week", ["a"])
    day = _write_conversations(tmp_path / "run" / "last_day", ["a"])
    out = tmp_path / "run" / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)

    code = main(["merge-agents", "--week", str(week), "--day", str(day), "--service", "vpn"])
    assert code == 0
    assert "Дашборд" in capsys.readouterr().out
    assert list(out.glob("dashboard_*.html"))


def test_merge_agents_results_uses_recommendations_file(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)
    rec_path = tmp_path / "recommendations.json"
    rec_path.write_text(json.dumps(["Рекомендация 1", "Рекомендация 2"], ensure_ascii=False), encoding="utf-8")

    outcome = merge_agents_results(week, day, out, recommendations=rec_path)

    result = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    assert result["recommendations"] == ["Рекомендация 1", "Рекомендация 2"]
    assert result["period_analysis"]["week"]["recommendations"] == []
    assert result["period_analysis"]["day"]["recommendations"] == []
    report = Path(outcome["report_path"]).read_text(encoding="utf-8")
    assert report.count("## Рекомендации") == 1
    assert "Рекомендация 1" in report


def test_merge_agents_results_rejects_bad_recommendations(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)
    rec_path = tmp_path / "recommendations.json"
    rec_path.write_text(json.dumps({"not": "a list"}, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AnalysisError, match="списком непустых строк"):
        merge_agents_results(week, day, out, recommendations=rec_path)


def test_merge_agents_results_accepts_recommendations_list(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)

    outcome = merge_agents_results(week, day, out, recommendations=["  Общий совет  "])

    result = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    assert result["recommendations"] == ["Общий совет"]


def test_cli_merge_agents_defaults_to_latest_run(tmp_path, monkeypatch, capsys):
    from intercom_exporter.support_analysis import main

    run = tmp_path / "data" / "20261102T222417Z_20261103T222417Z"
    week = _write_conversations(run / "last_week", ["a"])
    day = _write_conversations(run / "last_day", ["a"])
    out = run / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)
    monkeypatch.chdir(tmp_path)

    code = main(["merge-agents"])

    assert code == 0
    assert "Дашборд" in capsys.readouterr().out
    assert list(out.glob("dashboard_*.html"))


def test_cli_merge_agents_picks_up_agents_work_recommendations(tmp_path, monkeypatch):
    from intercom_exporter.support_analysis import main

    run = tmp_path / "data" / "run1"
    week = _write_conversations(run / "last_week", ["a"])
    day = _write_conversations(run / "last_day", ["a"])
    out = run / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)
    rec_path = out / "agents_work" / "recommendations.json"
    rec_path.write_text(json.dumps(["Общий совет из бандла"], ensure_ascii=False), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["merge-agents"]) == 0

    result = json.loads((out / "chatgpt_result.json").read_text(encoding="utf-8"))
    assert result["recommendations"] == ["Общий совет из бандла"]


def test_cli_render_dashboard_from_existing_result(tmp_path, monkeypatch):
    from intercom_exporter.support_analysis import main

    run = tmp_path / "data" / "run1"
    week = _write_conversations(run / "last_week", ["a"])
    day = _write_conversations(run / "last_day", ["a"])
    out = run / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)
    monkeypatch.chdir(tmp_path)
    assert main(["merge-agents"]) == 0
    next(out.glob("dashboard_*.html")).unlink()

    code = main(["render-dashboard", "--dashboard-dir", str(tmp_path / "dash")])

    assert code == 0
    assert list((tmp_path / "dash").glob("dashboard_*.html"))


def test_cli_research_waits_for_orchestration(tmp_path, monkeypatch, capsys):
    from intercom_exporter.support_analysis import main

    run = tmp_path / "data" / "run1"
    _write_conversations(run / "last_week", ["a"])
    _write_conversations(run / "last_day", ["a"])
    monkeypatch.chdir(tmp_path)

    code = main(["research"])

    assert code == 3
    assert "Оркестрация не завершена" in capsys.readouterr().out
    assert (run / "support-analysis" / "agents_work" / "manifest.json").is_file()


def test_merge_agents_results_defaults_recommendations_from_stats(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)

    outcome = merge_agents_results(week, day, out)

    result = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    recommendations = result["recommendations"]
    assert recommendations
    assert "Сформируйте рекомендации" not in recommendations[0]
    assert "Проблемы подключения VPN" in recommendations[0]
    assert "2 обращений" in recommendations[0]
    report = Path(outcome["report_path"]).read_text(encoding="utf-8")
    assert recommendations[0] in report
    dashboard = Path(outcome["dashboard_path"]).read_text(encoding="utf-8")
    assert recommendations[0] in dashboard


def test_merge_agents_results_default_recommendations_flag_high_severity(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)
    entry = manifest["chunks"][0]
    result = json.loads(Path(entry["result_path"]).read_text(encoding="utf-8"))
    result["classifications"][0]["severity"] = "high"
    Path(entry["result_path"]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    outcome = merge_agents_results(week, day, out)

    merged = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    assert any("высокой тяжести: 1" in item for item in merged["recommendations"])


def test_cli_research_merges_and_builds_dashboard(tmp_path, monkeypatch):
    from intercom_exporter.support_analysis import main

    run = tmp_path / "data" / "run1"
    week = _write_conversations(run / "last_week", ["a"])
    day = _write_conversations(run / "last_day", ["a"])
    out = run / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest, category=None)
    (out / "agents_work" / "recommendations.json").write_text(
        json.dumps(["Совет"], ensure_ascii=False), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code = main(["research", "--dashboard-dir", str(tmp_path / "dash")])

    assert code == 0
    assert list((tmp_path / "dash").glob("dashboard_*.html"))
    result = json.loads((out / "chatgpt_result.json").read_text(encoding="utf-8"))
    assert result["recommendations"] == ["Совет"]


def _conv(customer_text="", operator_text=""):
    messages = []
    if customer_text:
        messages.append({"role": "customer", "text": customer_text})
    if operator_text:
        messages.append({"role": "operator", "text": operator_text})
    return {"id": "1", "title": "", "messages": messages}


@pytest.mark.parametrize("raw,expected", [
    ("Отмена подписки", "Отмена подписки"),
    ("отменить подписку", "Отмена подписки"),
    ("iOS-приложение", "iOS-приложение"),
    ("Расширение Chrome", "Расширение Chrome"),
    ("Подписка не активна", "Подписка не активна"),
    ("Сайт не открывается", "Сайт или сервис недоступен"),
    ("Сервис недоступен", "Сайт или сервис недоступен"),
])
def test_normalize_subcategory_maps_variants(raw, expected):
    assert _normalize_subcategory(raw) == expected


def test_operator_tariff_instruction_does_not_override_customer_request():
    conv = _conv(
        customer_text="хочу отменить подписку",
        operator_text='У вас тариф "Для документов", приложение работает только на тарифах "Для Видео" и "Персональный"',
    )
    assert _apply_fixed_subcategory_rules("Отмена подписки", conv) == "Отмена подписки"


def test_apply_rules_prioritizes_cancel_over_ios():
    conv = _conv(customer_text="хочу отменить подписку, у меня iPhone")
    assert _apply_fixed_subcategory_rules("Отмена подписки", conv) == "Отмена подписки"


def test_apply_rules_detects_cancel_in_non_billing_category():
    conv = _conv(customer_text="отмените подписку, VPN не подключается")
    assert _apply_fixed_subcategory_rules("Отмена подписки", conv) == "Отмена подписки"


def test_apply_rules_does_not_infer_subcategory_from_platform_markers():
    assert _apply_fixed_subcategory_rules("Проблема не уточнена", _conv(customer_text="не работает на айфоне")) == "Проблема не уточнена"


def test_apply_rules_keeps_unknown_subcategory_without_markers():
    conv = _conv(customer_text="просто спасибо")
    assert _apply_fixed_subcategory_rules("Благодарность", conv) == "Благодарность"


@pytest.mark.parametrize("raw,expected", [
    ("Код не приходит", "Код не приходит"),
    ("Ошибка входа", "Ошибка входа"),
    ("Видео не загружается", "Видео не загружается"),
    ("Низкая скорость", "Низкая скорость"),
])
def test_platform_mentions_never_replace_problem_subcategory(raw, expected):
    conv = _conv(customer_text="В Chrome на iPhone после оплаты видео не загружается")
    assert _apply_fixed_subcategory_rules(raw, conv) == expected
    assert _apply_hypertube_fixed_subcategory_rules(raw, conv) == expected


def test_finalization_keeps_legacy_results_and_normalizes_platforms():
    legacy = {"id": "a", "category": "Проблемы подключения VPN", "subcategory": "Ошибка входа",
              "confidence": "high"}
    assert _normalized_classification(legacy)["platforms"] == []
    modern = {**legacy, "platforms": [" iOS ", "неизвестный клиент", "iOS"]}
    assert _normalized_classification(modern)["platforms"] == ["iOS", "неизвестный клиент"]


def test_finalization_collapses_platform_aliases():
    item = {
        "id": "c1", "category": "Оплата и подписка", "subcategory": "Отмена подписки",
        "platforms": ["OpenVPN Connect", "OpenVPN", "Windows 11", "Mac OS", "Расширение Chrome"],
        "problem_summary": "Запрос отмены", "severity": None, "severity_reason": "—",
        "confidence": "high", "evidence": "Хочу отменить",
    }

    assert _normalized_classification(item)["platforms"] == ["OpenVPN", "Windows", "macOS", "Расширение Chrome"]


def test_finalization_adds_tariff_restriction_without_overriding_refund():
    from intercom_exporter.agents_orchestration import _finalize_classification
    item = {
        "id": "c1", "category": "Оплата и подписка", "subcategory": "Возврат денег",
        "problem_summary": "Вернуть деньги", "severity": None, "confidence": "high",
    }
    conversation = {"messages": [{"role": "operator", "text": 'Доступ к приложению не предусмотрен тарифом «Для документов».'}]}

    result = _finalize_classification(item, conversation)

    assert result["subcategory"] == "Возврат денег"
    assert result["additional_topics"] == ['Ограничение тарифа «Для документов»']


def test_merge_preserves_problem_and_separates_platforms(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)
    _fill_results(manifest)
    for entry in manifest["chunks"]:
        chunk = json.loads(Path(entry["chunk_path"]).read_text(encoding="utf-8"))
        Path(entry["result_path"]).write_text(json.dumps({"classifications": [
            {"id": row["id"], "category": "Проблемы подключения VPN", "subcategory": "Низкая скорость",
             "platforms": [" Android ", "неизвестная платформа", "Android"], "problem_summary": "Падает соединение", "severity": None,
             "severity_reason": "не уточнено", "confidence": "high", "evidence": "VPN отключается"}
            for row in chunk["conversations"]]}, ensure_ascii=False), encoding="utf-8")
    outcome = merge_agents_results(week, day, out)
    result = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    assert result["classifications"][0]["subcategory"] == "Низкая скорость"
    assert result["classifications"][0]["platforms"] == ["Android", "неизвестная платформа"]


def test_apply_rules_detects_document_tariff_by_admin_marker():
    conv = _conv(
        customer_text="подписка неактивна",
        operator_text='Для документов не предусмотрено приложение, используйте OpenVPN Connect',
    )
    assert _apply_fixed_subcategory_rules("Подписка не активировалась", conv) == "Подписка не активировалась"


def test_subcategories_are_not_a_fixed_platform_list():
    from intercom_exporter.agents_orchestration import get_fixed_subcategories
    assert get_fixed_subcategories() == []


def test_merge_agents_results_applies_fixed_subcategory_rules(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b", "c"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2)

    # Заполняем result с вариантами написания, которые должны нормализоваться
    for entry in manifest["chunks"]:
        chunk = json.loads(Path(entry["chunk_path"]).read_text(encoding="utf-8"))
        items = []
        for conv in chunk["conversations"]:
            if conv["id"] == "a":
                items.append({
                    "id": "a", "category": "Оплата и подписка",
                    "subcategory": "Ошибка входа", "platforms": ["iOS"], "problem_summary": "—",
                    "severity": None, "severity_reason": "—",
                    "confidence": "high", "evidence": "",
                })
            elif conv["id"] == "b":
                items.append({
                    "id": "b", "category": "Проблемы подключения VPN",
                    "subcategory": "Код не приходит", "platforms": ["iOS"], "problem_summary": "—",
                    "severity": None, "severity_reason": "—",
                    "confidence": "high", "evidence": "",
                })
            else:
                items.append({
                    "id": conv["id"], "category": "Установка и настройка",
                    "subcategory": "—", "problem_summary": "—",
                    "severity": None, "severity_reason": "—",
                    "confidence": "low", "evidence": "",
                })
        Path(entry["result_path"]).write_text(
            json.dumps({"classifications": items}, ensure_ascii=False), encoding="utf-8")

    outcome = merge_agents_results(week, day, out)

    result = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in result["classifications"]}
    assert by_id["a"]["subcategory"] == "Ошибка входа"
    assert by_id["b"]["subcategory"] == "Код не приходит"
    assert by_id["b"]["platforms"] == ["iOS"]


def _hypertube_conv(customer_text="", operator_text=""):
    messages = []
    if customer_text:
        messages.append({"role": "customer", "text": customer_text})
    if operator_text:
        messages.append({"role": "operator", "text": operator_text})
    return {"title": "", "messages": messages}


@pytest.mark.parametrize("raw,expected", [
    ("Отмена подписки", "Отмена подписки"),
    ("Расширение Chrome", "Расширение Chrome"),
    ("Android", "Android"),
    ("TV/приставка", "TV/приставка"),
])
def test_normalize_hypertube_subcategory_maps_variants(raw, expected):
    assert _normalize_hypertube_subcategory(raw) == expected


def test_hypertube_rules_prioritize_cancel():
    conv = _hypertube_conv(customer_text="хочу отменить подписку, у меня chrome")
    assert _apply_hypertube_fixed_subcategory_rules("Отмена подписки", conv) == "Отмена подписки"


def test_hypertube_rules_prioritize_chrome_over_other_browsers():
    conv = _hypertube_conv(customer_text="не работает в chrome и opera")
    assert _apply_hypertube_fixed_subcategory_rules("Видео не загружается", conv) == "Видео не загружается"


def test_hypertube_rules_detects_yandex():
    conv = _hypertube_conv(customer_text="не работает в яндекс браузере")
    assert _apply_hypertube_fixed_subcategory_rules("Проблема не уточнена", conv) == "Проблема не уточнена"


def test_hypertube_rules_detects_tv():
    conv = _hypertube_conv(customer_text="не работает на телевизоре")
    assert _apply_hypertube_fixed_subcategory_rules("Проблема не уточнена", conv) == "Проблема не уточнена"


def test_hypertube_rules_detects_android():
    conv = _hypertube_conv(customer_text="не работает на андроид")
    assert _apply_hypertube_fixed_subcategory_rules("Проблема не уточнена", conv) == "Проблема не уточнена"


def test_hypertube_rules_detects_windows():
    conv = _hypertube_conv(customer_text="не работает на компьютере")
    assert _apply_hypertube_fixed_subcategory_rules("Проблема не уточнена", conv) == "Проблема не уточнена"


def test_hypertube_rules_keeps_unknown_without_markers():
    conv = _hypertube_conv(customer_text="просто спасибо")
    assert _apply_hypertube_fixed_subcategory_rules("Благодарность", conv) == "Благодарность"


def test_hypertube_problem_subcategory_survives_platform_and_subscription_mentions():
    conv = _hypertube_conv(customer_text='На телевизоре подписка активна, но YouTube пишет «нет интернета»')
    assert _apply_hypertube_fixed_subcategory_rules("Нет подключения к интернету", conv) == "Нет подключения к интернету"


def test_hypertube_subcategories_are_not_a_fixed_platform_list():
    from intercom_exporter.agents_orchestration import get_fixed_subcategories
    assert get_fixed_subcategories("hypertube") == []


def test_merge_agents_results_applies_hypertube_fixed_subcategory_rules(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b", "c"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, schema_version=1, workers=2, service_id="hypertube")

    for entry in manifest["chunks"]:
        chunk = json.loads(Path(entry["chunk_path"]).read_text(encoding="utf-8"))
        items = []
        for conv in chunk["conversations"]:
            if conv["id"] == "a":
                items.append({
                    "id": "a", "category": "Оплата и подписка",
                    "subcategory": "Возврат денег", "platforms": ["Расширение Chrome"], "problem_summary": "—",
                    "severity": None, "severity_reason": "—",
                    "confidence": "high", "evidence": "",
                })
            elif conv["id"] == "b":
                items.append({
                    "id": "b", "category": "Приложение",
                    "subcategory": "Видео не загружается", "platforms": ["Android"], "problem_summary": "—",
                    "severity": None, "severity_reason": "—",
                    "confidence": "high", "evidence": "",
                })
            else:
                items.append({
                    "id": conv["id"], "category": "Установка и настройка",
                    "subcategory": "—", "problem_summary": "—",
                    "severity": None, "severity_reason": "—",
                    "confidence": "low", "evidence": "",
                })
        Path(entry["result_path"]).write_text(
            json.dumps({"classifications": items}, ensure_ascii=False), encoding="utf-8")

    outcome = merge_agents_results(week, day, out, service_id="hypertube")

    result = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in result["classifications"]}
    assert by_id["a"]["subcategory"] == "Возврат денег"
    assert by_id["b"]["subcategory"] == "Видео не загружается"
    assert by_id["a"]["platforms"] == ["Расширение Chrome"]

import csv
import json
import os

import pytest

from intercom_exporter.support_analysis import (
    AnalysisError,
    _build_payload,
    _normalize_platforms,
    _docs_tariff_operator_evidence,
    find_latest_run,
    generate_report,
    main,
    prepare_analysis,
    sanitize_text,
)


@pytest.mark.parametrize(
    "values, expected",
    [
        (["OpenVPN Connect", "OpenVPN"], ["OpenVPN"]),
        (["OpenVPN на iOS"], ["OpenVPN", "iOS"]),
        (["Компьютер", "Windows 11", "Mac OS"], ["Windows", "macOS"]),
        (["Расширение браузера", "Расширение Яндекс", "Chrome"],
         ["Расширение браузера (браузер не указан)", "Расширение Яндекс", "Расширение Chrome"]),
        (["Расширение Firefox"], ["Расширение другого браузера"]),
        (["VPN Наоборот", "Приложение VPN Наоборот"], ["Приложение VPN Наоборот"]),
        (["Компьютер", "Неизвестный клиент"], ["Компьютер (ОС не указана)", "Неизвестный клиент"]),
        (["Телефон", "App Store", "Египет", "Разные устройства"], []),
    ],
)
def test_normalize_platforms_groups_synonyms_without_inventing_details(values, expected):
    assert _normalize_platforms(values) == expected


def test_normalize_platforms_uses_customer_context_to_resolve_generic_platforms():
    assert _normalize_platforms(["Компьютер"], "Я пользуюсь Windows 11") == ["Windows"]
    assert _normalize_platforms(["Расширение браузера"], "Установлено в Яндекс Браузере") == ["Расширение Яндекс"]
    assert _normalize_platforms(["Приложение"], "Приложение VPN Наоборот не запускается") == ["Приложение VPN Наоборот"]
    assert _normalize_platforms(["Телефон"], "Не работает на iPhone") == ["iOS"]


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def sample_conversations():
    return [
        {
            "id": "conv-1",
            "title": "Не работает VPN для ivan@example.com",
            "created_at": "2026-10-27T00:00:00Z",
            "source": {"body": "Не подключается VPN. Телефон +7 911 123-45-67. Сайт https://example.org/help"},
            "conversation_parts": [
                {"part_type": "comment", "author": {"type": "user"}, "body": "Напишите пароль: secret-value"},
                {"part_type": "comment", "author": {"type": "admin"}, "body": "Проверьте подключение."},
                {"part_type": "close", "author": {"type": "admin"}, "body": None},
            ],
        },
        {
            "id": "conv-2",
            "title": "Оплата",
            "source": {"body": "С карты списались деньги дважды."},
            "conversation_parts": [],
        },
    ]


def valid_result(fingerprint):
    return {
        "complete": True,
        "conversation_count": 2,
        "dataset_fingerprint": fingerprint,
        "categories": [{"name": "Подключение VPN", "description": "Проблемы с подключением."},
                       {"name": "Оплата", "description": "Списания и возвраты."}],
        "classifications": [
            {"id": "conv-1", "category": "Подключение VPN", "subcategory": "Не подключается",
             "problem_summary": "Клиент не может подключиться.", "severity": "high",
             "severity_reason": "Сервис недоступен клиенту.", "confidence": "high",
             "evidence": "Не подключается VPN."},
            {"id": "conv-2", "category": "Оплата", "subcategory": "Повторное списание",
             "problem_summary": "Деньги списаны дважды.", "severity": "medium",
             "severity_reason": "Финансовая проблема клиента.", "confidence": "medium",
             "evidence": "списались деньги дважды"},
        ],
        "executive_summary": "Частая тема — подключение VPN.",
        "recommendations": ["Проверить причины сбоев подключения."],
    }


def test_sanitizer_removes_sensitive_patterns_without_rewriting_other_text():
    text = "Письмо ivan@example.com, телефон +7 911 123-45-67, пароль: secret-value, ссылка https://example.org/a. Оставить это."
    sanitized = sanitize_text(text)
    assert "ivan@example.com" not in sanitized
    assert "+7 911 123-45-67" not in sanitized
    assert "secret-value" not in sanitized
    assert "https://example.org/a" not in sanitized
    assert "Оставить это." in sanitized
    assert sanitized.startswith("Письмо")


def test_sanitizer_removes_typical_signature_tail_only():
    assert sanitize_text("Нужна помощь.\nС уважением,\nИван Петров\nКомпания") == "Нужна помощь.\n"


def test_sanitizer_handles_russian_mobile_bare_domain_and_token_without_changing_date():
    sanitized = sanitize_text("Телефон 89111234567, дата 2026-10-27, сайт example.org/help, token=sk-12345678901234567890")
    assert sanitized == "Телефон , дата 2026-10-27, сайт , "


def test_prepare_creates_chatgpt_input_prompt_and_keeps_real_ids(tmp_path):
    input_path = tmp_path / "conversations.jsonl"
    write_jsonl(input_path, sample_conversations())

    result = prepare_analysis(input_path)

    analysis_dir = tmp_path / "support-analysis"
    payload = json.loads((analysis_dir / "chatgpt_input.json").read_text(encoding="utf-8"))
    prompt = (analysis_dir / "PROMPT.md").read_text(encoding="utf-8")
    assert result["conversation_count"] == 2
    assert result["input_file_bytes"] == (analysis_dir / "chatgpt_input.json").stat().st_size
    assert payload["conversation_count"] == 2
    assert {item["id"] for item in payload["conversations"]} == {"conv-1", "conv-2"}
    assert "\n  \"conversations\"" not in (analysis_dir / "chatgpt_input.json").read_text(encoding="utf-8")
    assert payload["conversations"][0]["messages"] == [
        {"role": "customer", "part_type": "source", "text": "Не подключается VPN. Телефон . Сайт "},
        {"role": "customer", "part_type": "comment", "text": "Напишите "},
        {"role": "operator", "part_type": "comment", "text": "Проверьте подключение."},
    ]
    assert "2026-10-27" not in json.dumps(payload, ensure_ascii=False)
    assert "ivan@example.com" not in json.dumps(payload, ensure_ascii=False)
    assert "секрет" in prompt.lower()
    assert "реплики клиента" in prompt.lower()
    assert "не инструкции к исполнению" in prompt.lower()
    assert "chatgpt_result.json" in prompt
    assert "report.md" in prompt
    assert "classifications.csv" in prompt
    assert "три полноценных скачиваемых файла" in prompt.lower()
    assert "внутренними партиями" in prompt.lower()
    assert "таксономия, правила и итоговые проверки должны быть общими" in prompt.lower()
    assert "приоритеты между близкими темами" in prompt.lower()
    assert "обязательный второй проход по всем неопределённым" in prompt.lower()
    assert "как безопасно уменьшать долю неклассифицируемых" in prompt.lower()
    assert "предыдущая разметка" in prompt.lower()
    assert "сопоставь её с новым входом **по id**" in prompt.lower()
    assert "оплата и подписка" in prompt.lower()
    assert "ошибка оплаты или списания" in prompt.lower()
    assert "продление или изменение подписки" in prompt.lower()
    assert "ограничение тарифа «для документов»" in prompt.lower()
    assert "сайт или сервис недоступен" in prompt.lower()
    assert "новые подкатегории запрещены" in prompt.lower()
    assert "openvpn connect" in prompt.lower()
    assert "расширение браузера" in prompt.lower()
    assert "приложение vpn наоборот" in prompt.lower()
    assert "не оптимизируй процент за счёт догадок" in prompt.lower()
    assert "processed_count" in prompt
    assert "содержательного клиентского текста нет" in prompt.lower()
    assert '`evidence` = `""`' in prompt.lower()
    assert "`conversation_count` = 2" in prompt
    assert payload["dataset_fingerprint"] in prompt
    assert "5841" not in prompt


def test_prepare_reads_pretty_json_array_exports(tmp_path):
    input_path = tmp_path / "conversations.json"
    rows = sample_conversations()
    rows[0]["conversation_parts"] = {
        "type": "conversation_part.list",
        "conversation_parts": [
            {"part_type": "comment", "author": {"type": "user", "email": "person@example.com"},
             "body": "Клиент написал пароль: secret-value"},
            {"part_type": "comment", "author": {"type": "admin"}, "body": "Ответ оператора"},
        ],
        "total_count": 2,
    }
    input_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    result = prepare_analysis(input_path)

    payload = json.loads((tmp_path / "support-analysis" / "chatgpt_input.json").read_text(encoding="utf-8"))
    assert result["conversation_count"] == 2
    assert {item["id"] for item in payload["conversations"]} == {"conv-1", "conv-2"}
    assert payload["conversations"][0]["messages"][-2:] == [
        {"role": "customer", "part_type": "comment", "text": "Клиент написал "},
        {"role": "operator", "part_type": "comment", "text": "Ответ оператора"},
    ]


def test_prepare_rejects_duplicate_or_missing_conversation_ids(tmp_path):
    path = tmp_path / "duplicate.jsonl"
    write_jsonl(path, [{"id": "same"}, {"id": "same"}])
    with pytest.raises(AnalysisError, match="повторяющийся ID"):
        prepare_analysis(path)

    write_jsonl(path, [{"title": "without id"}])
    with pytest.raises(AnalysisError, match="нет ID"):
        prepare_analysis(path)


def test_prepare_supports_ten_thousand_short_conversations_in_one_compact_file(tmp_path):
    input_path = tmp_path / "many.jsonl"
    input_path.write_text(
        "".join(json.dumps({"id": f"case-{index}", "source": {"body": "Не работает VPN"}}, ensure_ascii=False) + "\n"
                  for index in range(10_000)),
        encoding="utf-8",
    )

    result = prepare_analysis(input_path)

    analysis_dir = tmp_path / "support-analysis"
    payload = json.loads((analysis_dir / "chatgpt_input.json").read_text(encoding="utf-8"))
    prompt = (analysis_dir / "PROMPT.md").read_text(encoding="utf-8")
    assert result["conversation_count"] == 10_000
    assert payload["conversation_count"] == 10_000
    assert len(payload["conversations"]) == 10_000
    assert len({item["id"] for item in payload["conversations"]}) == 10_000
    assert "10 000" in prompt or "10000" in prompt
    assert result["input_file_bytes"] < input_path.stat().st_size * 2


def test_report_counts_unique_conversations_and_writes_csv_and_markdown(tmp_path):
    input_path = tmp_path / "conversations.jsonl"
    write_jsonl(input_path, sample_conversations())
    prepared = prepare_analysis(input_path)
    result_path = tmp_path / "support-analysis" / "chatgpt_result.json"
    result_path.write_text(json.dumps(valid_result(prepared["dataset_fingerprint"]), ensure_ascii=False), encoding="utf-8")

    report = generate_report(input_path, result_path)

    output_dir = tmp_path / "support-analysis"
    markdown = (output_dir / "report.md").read_text(encoding="utf-8")
    with (output_dir / "classifications.csv").open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert report["conversation_count"] == 2
    assert report["category_counts"] == {"Подключение VPN": 1, "Оплата": 1}
    assert report["severity_counts"] == {"high": 1, "medium": 1, "low": 0, "unknown": 0}
    assert len(csv_rows) == 2
    assert "platforms" in csv_rows[0]
    assert csv_rows[0]["platforms"] == ""
    assert "Частота проблем" in markdown
    assert "Тяжесть по текстовым признакам" in markdown
    assert "Проверить причины сбоев подключения." in markdown


def test_report_accepts_empty_evidence_when_customer_text_is_missing(tmp_path):
    input_path = tmp_path / "conversations.jsonl"
    write_jsonl(input_path, sample_conversations())
    prepared = prepare_analysis(input_path)
    result = valid_result(prepared["dataset_fingerprint"])
    result["classifications"][0]["evidence"] = ""
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    assert generate_report(input_path, result_path)["conversation_count"] == 2


def test_report_writes_platforms_and_additional_topics_to_json_csv_and_report(tmp_path):
    input_path = tmp_path / "conversations.jsonl"
    write_jsonl(input_path, sample_conversations())
    prepared = prepare_analysis(input_path)
    result = valid_result(prepared["dataset_fingerprint"])
    result["classifications"][0]["subcategory"] = "Отмены"
    result["classifications"][0]["platforms"] = ["iOS", "Chrome"]
    result["classifications"][0]["additional_topics"] = ["Низкая скорость"]
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    generate_report(input_path, result_path)

    output_dir = tmp_path / "support-analysis"
    normalized = json.loads(result_path.read_text(encoding="utf-8"))
    assert normalized["classifications"][0]["platforms"] == ["iOS", "Chrome"]
    assert normalized["classifications"][0]["additional_topics"] == ["Низкая скорость"]
    with (output_dir / "classifications.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["platforms"] == "iOS;Расширение Chrome"
    assert rows[1]["platforms"] == ""
    assert rows[0]["subcategory"] == "Отмена подписки"
    assert rows[0]["additional_topics"] == "Низкая скорость"
    markdown = (output_dir / "report.md").read_text(encoding="utf-8")
    assert "Платформы" in markdown
    assert "Топ-10 подкатегорий: проблемы и запросы" in markdown
    assert "Отмена подписки" in markdown
    assert "iOS" in markdown and "Chrome" in markdown


def test_report_tracks_docs_tariff_in_primary_and_additional_topics(tmp_path):
    input_path = tmp_path / "conversations.jsonl"
    write_jsonl(input_path, sample_conversations())
    prepared = prepare_analysis(input_path)
    result = valid_result(prepared["dataset_fingerprint"])
    result["classifications"][0]["subcategory"] = "Ограничение тарифа «Для документов»"
    result["classifications"][1]["additional_topics"] = ["Ограничение тарифа «Для документов»"]
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    generate_report(input_path, result_path)

    markdown = (tmp_path / "support-analysis" / "report.md").read_text(encoding="utf-8")
    assert "## Ограничение тарифа «Для документов»" in markdown
    assert "Подтверждено обращений: **2**." in markdown


@pytest.mark.parametrize("text, expected", [
    ('Доступ к приложению не предусмотрен тарифом «Для документов». OpenVPN доступен.', True),
    ('Наше приложение VPN Naoborot работает только на тарифах "Для Видео" и "Персональный". У Вас тариф "Для Документов".', True),
    ('Вы купили подписку "Для документов". На этом тарифе подключение через наше приложение не предусмотрено.', True),
    ('Вы оформили подписку "Для документов". Чтобы работало наше приложение на iPhone нужна подписка минимум "Для Видео".', True),
    ('На тарифе "Для документов" скорость 1 Мбит/с.', False),
    ('У вас тариф "Для документов". Подключитесь через приложение OpenVPN.', False),
    ('Вашу подписку "Для документов" отменили. Возврат запросили.', False),
])
def test_docs_tariff_detection_uses_explicit_operator_restriction(text, expected):
    conversation = {"messages": [{"role": "operator", "text": text}]}
    assert bool(_docs_tariff_operator_evidence(conversation)) is expected
    assert not _docs_tariff_operator_evidence({"messages": [{"role": "customer", "text": text}]})


def test_report_adds_operator_confirmed_tariff_topic_and_counts_conversation_once(tmp_path):
    rows = sample_conversations()
    message = {"author": {"type": "admin"}, "body": 'Доступ к приложению не предусмотрен тарифом «Для документов».'}
    rows[0]["conversation_parts"] = [message, dict(message)]
    input_path = tmp_path / "conversations.jsonl"
    write_jsonl(input_path, rows)
    prepared = prepare_analysis(input_path)
    result = valid_result(prepared["dataset_fingerprint"])
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    generate_report(input_path, result_path)

    with (tmp_path / "support-analysis/classifications.csv").open(encoding="utf-8-sig", newline="") as handle:
        classified = list(csv.DictReader(handle))
    assert classified[0]["subcategory"] == result["classifications"][0]["subcategory"]
    assert classified[0]["additional_topics"] == 'Ограничение тарифа «Для документов»'
    assert "Подтверждено обращений: **1**." in (tmp_path / "support-analysis/report.md").read_text(encoding="utf-8")


def test_report_rejects_non_list_platforms(tmp_path):
    input_path = tmp_path / "conversations.jsonl"
    write_jsonl(input_path, sample_conversations())
    prepared = prepare_analysis(input_path)
    result = valid_result(prepared["dataset_fingerprint"])
    result["classifications"][0]["platforms"] = "iOS"
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AnalysisError, match="platforms должен быть списком"):
        generate_report(input_path, result_path)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda value: value["classifications"].pop(), "пропущены обращения"),
        (lambda value: value["classifications"].append(value["classifications"][0]), "повторяется"),
        (lambda value: value["classifications"][0].update(id="unknown"), "неизвестный ID"),
        (lambda value: value["classifications"][0].update(category="Нет такой категории"), "неизвестная категория"),
        (lambda value: value["classifications"][0].update(severity="critical"), "severity"),
        (lambda value: value.update(dataset_fingerprint="stale"), "dataset_fingerprint"),
        (lambda value: value.update(conversation_count=1), "conversation_count"),
        (lambda value: value.update(complete=False), "непол"),
    ],
)
def test_report_rejects_invalid_chatgpt_classification(tmp_path, mutate, message):
    input_path = tmp_path / "conversations.jsonl"
    write_jsonl(input_path, sample_conversations())
    prepared = prepare_analysis(input_path)
    value = valid_result(prepared["dataset_fingerprint"])
    mutate(value)
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AnalysisError, match=message):
        generate_report(input_path, result_path)


def test_cli_prepare_and_report_commands_work_without_api_credentials(tmp_path, monkeypatch):
    input_path = tmp_path / "conversations.jsonl"
    write_jsonl(input_path, sample_conversations())
    monkeypatch.chdir(tmp_path)
    assert main(["prepare", "--input", str(input_path)]) == 0
    result_path = tmp_path / "support-analysis" / "chatgpt_result.json"
    assert not result_path.exists()
    assert (tmp_path / "support-analysis" / "chatgpt_input.json").is_file()
    assert (tmp_path / "support-analysis" / "PROMPT.md").is_file()
    payload = json.loads((tmp_path / "support-analysis" / "chatgpt_input.json").read_text(encoding="utf-8"))
    result_path.write_text(json.dumps(valid_result(payload["dataset_fingerprint"]), ensure_ascii=False), encoding="utf-8")
    assert main(["report", "--input", str(input_path), "--result", str(result_path)]) == 0


def test_prepare_monitoring_creates_two_inputs_and_one_shared_prompt(tmp_path):
    from intercom_exporter.support_analysis import prepare_monitoring_analysis

    week_dir, day_dir = tmp_path / "last_week", tmp_path / "last_day"
    week_dir.mkdir()
    day_dir.mkdir()
    week_path = week_dir / "conversations.json"
    day_path = day_dir / "conversations.json"
    week_rows = sample_conversations()
    day_rows = [week_rows[0]]
    week_path.write_text(json.dumps(week_rows, ensure_ascii=False), encoding="utf-8")
    day_path.write_text(json.dumps(day_rows, ensure_ascii=False), encoding="utf-8")

    prepared = prepare_monitoring_analysis(week_path, day_path)

    analysis_dir = tmp_path / "support-analysis"
    week = json.loads((analysis_dir / "chatgpt_input_week.json").read_text(encoding="utf-8"))
    day = json.loads((analysis_dir / "chatgpt_input_day.json").read_text(encoding="utf-8"))
    prompt = (analysis_dir / "PROMPT.md").read_text(encoding="utf-8")
    assert week["conversation_count"] == 2
    assert day["conversation_count"] == 1
    assert prepared["unique_conversation_count"] == 2
    assert "chatgpt_input_week.json" in prompt
    assert "chatgpt_input_day.json" in prompt
    assert "одну классификацию" in prompt.lower()
    assert "report.md" in prompt
    assert not (analysis_dir / "last_week" / "PROMPT.md").exists()
    assert not (analysis_dir / "last_day" / "PROMPT.md").exists()


def test_generate_monitoring_report_validates_union_and_reports_both_windows(tmp_path):
    from intercom_exporter.support_analysis import generate_monitoring_report

    week_dir, day_dir = tmp_path / "last_week", tmp_path / "last_day"
    week_dir.mkdir()
    day_dir.mkdir()
    week_path = week_dir / "conversations.json"
    day_path = day_dir / "conversations.json"
    week_rows = sample_conversations()
    day_rows = [week_rows[0]]
    week_path.write_text(json.dumps(week_rows, ensure_ascii=False), encoding="utf-8")
    day_path.write_text(json.dumps(day_rows, ensure_ascii=False), encoding="utf-8")
    union_payload = _build_payload(week_rows)
    result = valid_result(union_payload["dataset_fingerprint"])
    result["period_analysis"] = {
        "week": {"conversation_count": 2, "dataset_fingerprint": _build_payload(week_rows)["dataset_fingerprint"], "executive_summary": "За неделю преобладали проблемы VPN.", "recommendations": ["Уточнять устройства."]},
        "day": {"conversation_count": 1, "dataset_fingerprint": _build_payload(day_rows)["dataset_fingerprint"], "executive_summary": "Сегодня был один случай подключения.", "recommendations": ["Проверять VPN-сценарий."]},
    }
    result_path = tmp_path / "chatgpt_result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    report = generate_monitoring_report(week_path, day_path, result_path)

    markdown = (tmp_path / "support-analysis" / "report.md").read_text(encoding="utf-8")
    with (tmp_path / "support-analysis" / "classifications.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert report["unique_conversation_count"] == 2
    assert report["week_conversation_count"] == 2
    assert report["day_conversation_count"] == 1
    assert len(rows) == 2
    assert rows[0]["periods"] == "week;day"
    assert rows[1]["periods"] == "week"
    assert "Последние 7 дней" in markdown
    assert "Последний день" in markdown
    assert "Сравнение периодов" in markdown
    assert "За неделю преобладали проблемы VPN." in markdown
    assert "Сегодня был один случай подключения." in markdown


def test_monitoring_report_rejects_wrong_window_fingerprint(tmp_path):
    from intercom_exporter.support_analysis import _build_payload, generate_monitoring_report

    week_rows = sample_conversations()
    day_rows = [week_rows[0]]
    week_path = tmp_path / "week.json"
    day_path = tmp_path / "day.json"
    week_path.write_text(json.dumps(week_rows, ensure_ascii=False), encoding="utf-8")
    day_path.write_text(json.dumps(day_rows, ensure_ascii=False), encoding="utf-8")
    result = valid_result(_build_payload(week_rows)["dataset_fingerprint"])
    result["period_analysis"] = {
        "week": {"conversation_count": 2, "dataset_fingerprint": _build_payload(week_rows)["dataset_fingerprint"], "executive_summary": "Резюме недели.", "recommendations": []},
        "day": {"conversation_count": 1, "dataset_fingerprint": "wrong", "executive_summary": "Резюме дня.", "recommendations": []},
    }
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AnalysisError, match="day.*fingerprint|fingerprint.*day"):
        generate_monitoring_report(week_path, day_path, result_path)


def test_cli_prepare_monitoring_and_report_monitoring_use_one_shared_output(tmp_path, capsys, monkeypatch):
    from intercom_exporter.support_analysis import _build_payload

    week_rows = sample_conversations()
    day_rows = [week_rows[0]]
    week_path, day_path = tmp_path / "week.json", tmp_path / "day.json"
    week_path.write_text(json.dumps(week_rows, ensure_ascii=False), encoding="utf-8")
    day_path.write_text(json.dumps(day_rows, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / "combined-analysis"
    monkeypatch.chdir(tmp_path)
    assert main(["prepare-monitoring", "--week", str(week_path), "--day", str(day_path), "--output", str(output_dir)]) == 0
    result = valid_result(_build_payload(week_rows)["dataset_fingerprint"])
    result["period_analysis"] = {
        "week": {"conversation_count": 2, "dataset_fingerprint": _build_payload(week_rows)["dataset_fingerprint"], "executive_summary": "Сводка недели.", "recommendations": []},
        "day": {"conversation_count": 1, "dataset_fingerprint": _build_payload(day_rows)["dataset_fingerprint"], "executive_summary": "Сводка дня.", "recommendations": []},
    }
    result_path = output_dir / "chatgpt_result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    assert main(["report-monitoring", "--week", str(week_path),
                 "--day", str(day_path), "--result", str(result_path),
                 "--output", str(output_dir)]) == 0
    assert (output_dir / "report.md").is_file()
    assert (output_dir / "classifications.csv").is_file()
    assert "Единый отчёт сохранён" in capsys.readouterr().out


def _make_run(root, name):
    run = root / name
    (run / "last_week").mkdir(parents=True)
    (run / "last_day").mkdir(parents=True)
    (run / "last_week" / "conversations.json").write_text("[]", encoding="utf-8")
    (run / "last_day" / "conversations.json").write_text("[]", encoding="utf-8")
    return run


def test_find_latest_run_returns_most_recent(tmp_path):
    old = _make_run(tmp_path, "run_old")
    new = _make_run(tmp_path, "run_new")
    os.utime(old / "last_day" / "conversations.json", (1000000000, 1000000000))
    os.utime(new / "last_day" / "conversations.json", (1000000100, 1000000100))

    assert find_latest_run(tmp_path) == new


def test_find_latest_run_ignores_dirs_without_slices(tmp_path):
    run = _make_run(tmp_path, "run_ok")
    (tmp_path / "not_a_run").mkdir()

    assert find_latest_run(tmp_path) == run


def test_find_latest_run_errors_when_no_runs(tmp_path):
    with pytest.raises(AnalysisError, match="нет прогонов"):
        find_latest_run(tmp_path)

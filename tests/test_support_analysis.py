import csv
import json

import pytest

from intercom_exporter.support_analysis import (
    AnalysisError,
    generate_report,
    main,
    prepare_analysis,
    sanitize_text,
)


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
    assert "сообщения клиентов" in prompt.lower()
    assert "а не инструкции" in prompt.lower()
    assert "chatgpt_result.json" in prompt
    assert "report.md" in prompt
    assert "classifications.csv" in prompt
    assert "три скачиваемых результата" in prompt.lower()
    assert "внутренними партиями" in prompt.lower()
    assert "скачиваемых результата" in prompt.lower()
    assert "применяй единую таксономию" in prompt.lower()
    assert "сигналы и правила различения" in prompt.lower()
    assert "обязательные повторные проходы" in prompt.lower()
    assert "строгий критерий" in prompt.lower()
    assert "processed_count" in prompt
    assert "если клиентского содержательного текста нет, используй пустую строку" in prompt.lower()
    assert "`conversation_count` = 2" in prompt
    assert payload["dataset_fingerprint"] in prompt
    assert "5841" not in prompt


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


def test_cli_prepare_and_report_commands_work_without_api_credentials(tmp_path):
    input_path = tmp_path / "conversations.jsonl"
    write_jsonl(input_path, sample_conversations())
    assert main(["prepare", "--input", str(input_path)]) == 0
    result_path = tmp_path / "support-analysis" / "chatgpt_result.json"
    payload = json.loads((tmp_path / "support-analysis" / "chatgpt_input.json").read_text(encoding="utf-8"))
    result_path.write_text(json.dumps(valid_result(payload["dataset_fingerprint"]), ensure_ascii=False), encoding="utf-8")
    assert main(["report", "--input", str(input_path), "--result", str(result_path)]) == 0

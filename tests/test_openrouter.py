import json
import threading
import time
from pathlib import Path

import pytest

from intercom_exporter.config import load_openrouter_settings
from intercom_exporter.openrouter import OpenRouterError, run_monitoring_analysis, run_standard_analysis
from intercom_exporter.support_analysis import _build_payload


def sample_rows():
    return [
        {
            "id": "conversation-1",
            "title": "Cannot connect",
            "source": {"body": "VPN не подключается, пишите на private@example.com"},
            "conversation_parts": [],
        }
    ]


def valid_result(rows):
    payload = _build_payload(rows)
    return {
        "complete": True,
        "conversation_count": len(rows),
        "dataset_fingerprint": payload["dataset_fingerprint"],
        "categories": [{"name": "Подключение VPN", "description": "Проблемы соединения."}],
        "classifications": [
            {
                "id": str(row["id"]),
                "category": "Подключение VPN",
                "subcategory": "Не подключается",
                "problem_summary": "VPN не подключается",
                "severity": "medium",
                "severity_reason": "Клиент сообщает о проблеме подключения.",
                "confidence": "high",
                "evidence": "VPN не подключается",
            }
            for row in rows
        ],
        "executive_summary": "Основная проблема — подключение VPN.",
        "recommendations": ["Проверить стабильность подключения."],
    }


class FakeResponse:
    status_code = 200

    def __init__(self, value):
        self.value = value

    def raise_for_status(self):
        return None

    def json(self):
        return self.value


def test_load_openrouter_settings_is_optional_and_has_default_model(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    assert load_openrouter_settings(tmp_path / "missing.env") is None

    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=test-key\n", encoding="utf-8")
    settings = load_openrouter_settings(env_file)
    assert settings.api_key == "test-key"
    assert settings.model == "anthropic/claude-sonnet-4.5"


def test_load_dahl_settings_is_optional_and_has_default_model(tmp_path, monkeypatch):
    from intercom_exporter.config import DEFAULT_DAHL_MODEL, load_dahl_settings

    monkeypatch.delenv("DAHL_API_KEY", raising=False)
    monkeypatch.delenv("DAHL_MODEL", raising=False)
    monkeypatch.delenv("DAHL_WORKERS", raising=False)
    assert load_dahl_settings(tmp_path / ".env") is None

    env_file = tmp_path / ".env"
    env_file.write_text("DAHL_API_KEY=test-key\n", encoding="utf-8")
    settings = load_dahl_settings(env_file)
    assert settings is not None
    assert settings.api_key == "test-key"
    assert settings.model == DEFAULT_DAHL_MODEL == "MiniMaxAI/MiniMax-M2.7"
    assert settings.workers == 3


@pytest.mark.parametrize("value", ["0", "11", "abc"])
def test_dahl_workers_must_be_between_1_and_10(tmp_path, monkeypatch, value):
    from intercom_exporter.config import load_dahl_settings

    monkeypatch.delenv("DAHL_API_KEY", raising=False)
    monkeypatch.delenv("DAHL_WORKERS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"DAHL_API_KEY=key\nDAHL_WORKERS={value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="DAHL_WORKERS"):
        load_dahl_settings(env_file)


def test_load_config_exposes_dahl_settings(tmp_path, monkeypatch):
    from intercom_exporter.config import load_config

    monkeypatch.delenv("DAHL_API_KEY", raising=False)
    monkeypatch.delenv("DAHL_MODEL", raising=False)
    monkeypatch.delenv("DAHL_WORKERS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "INTERCOM_ACCESS_TOKEN=token\nDAHL_API_KEY=dahl-key\nDAHL_MODEL=vendor/model\nDAHL_WORKERS=5\n",
        encoding="utf-8",
    )
    config = load_config(env_file)
    assert config.dahl_api_key == "dahl-key"
    assert config.dahl_model == "vendor/model"
    assert config.dahl_workers == 5

    env_file.write_text("INTERCOM_ACCESS_TOKEN=token\n", encoding="utf-8")
    monkeypatch.delenv("DAHL_API_KEY", raising=False)
    config = load_config(env_file)
    assert config.dahl_api_key is None


def test_split_batches_obeys_utf8_byte_limit_and_keeps_each_conversation_once():
    from intercom_exporter.openrouter import _split_batches

    conversations = [
        {"id": str(index), "title": "Тема", "messages": [{"role": "customer", "text": "абвгд " * 40}]}
        for index in range(7)
    ]

    batches = _split_batches(conversations, 600)

    assert len(batches) > 1
    assert [item["id"] for batch in batches for item in batch] == [str(index) for index in range(7)]
    assert all(sum(len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) for item in batch) <= 600 for batch in batches)


def test_split_batches_rejects_single_conversation_above_safe_limit():
    from intercom_exporter.openrouter import _split_batches

    conversation = {"id": "too-large", "title": "", "messages": [{"role": "customer", "text": "x" * 750_001}]}

    with pytest.raises(OpenRouterError, match="too-large"):
        _split_batches([conversation], 100)


def test_standard_analysis_sends_only_prepared_json_and_writes_all_outputs(tmp_path, monkeypatch):
    from intercom_exporter.support_analysis import prepare_analysis
    from intercom_exporter import openrouter

    source = tmp_path / "conversations.json"
    rows = sample_rows()
    source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    prepared = prepare_analysis(source)
    api_calls = []

    def fake_post(url, *, headers, json, timeout):
        api_calls.append((url, headers, json, timeout))
        return FakeResponse({"choices": [{"message": {"content": json_module.dumps(valid_result(rows), ensure_ascii=False)}}]})

    import json as json_module
    monkeypatch.setattr(openrouter.requests, "post", fake_post)

    result = run_standard_analysis(source, prepared["output_dir"], "test-secret", "test/model")

    assert len(api_calls) == 1
    url, headers, request_body, timeout = api_calls[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == "Bearer test-secret"
    assert request_body["model"] == "test/model"
    assert timeout > 0
    submitted = json_module.dumps(request_body, ensure_ascii=False)
    assert "private@example.com" not in submitted
    assert "VPN не подключается" in submitted
    output = Path(prepared["output_dir"])
    assert result["report_path"] == str(output / "report.md")
    assert (output / "chatgpt_result.json").is_file()
    assert (output / "report.md").is_file()
    assert (output / "classifications.csv").is_file()


def test_analysis_supports_dahl_endpoint_and_label(tmp_path, monkeypatch):
    import intercom_exporter.openrouter as openrouter
    from intercom_exporter.openrouter import DAHL_URL
    from intercom_exporter.support_analysis import prepare_analysis

    source = tmp_path / "conversations.json"
    rows = sample_rows()
    source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    prepared = prepare_analysis(source)
    calls = []

    def fake_request(api_key, model, prompt, user_content, **kwargs):
        calls.append((api_key, model, kwargs))
        return valid_result(rows)

    monkeypatch.setattr(openrouter, "_request_result", fake_request)
    result = run_standard_analysis(
        source, prepared["output_dir"], "dahl-secret", "MiniMaxAI/MiniMax-M2.7",
        base_url=DAHL_URL, provider_label="Dahl",
    )

    assert result["result_path"].endswith("chatgpt_result.json")
    assert calls and calls[0][0] == "dahl-secret"
    assert calls[0][1] == "MiniMaxAI/MiniMax-M2.7"
    assert calls[0][2]["base_url"] == DAHL_URL
    assert calls[0][2]["provider_label"] == "Dahl"


def test_request_result_uses_given_base_url(monkeypatch):
    import intercom_exporter.openrouter as openrouter

    seen = {}

    class FakeOkResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{\"ok\": true}"}}]}

    def fake_post(url, **kwargs):
        seen["url"] = url
        return FakeOkResponse()

    monkeypatch.setattr(openrouter.requests, "post", fake_post)
    assert openrouter._request_result("k", "m", "p", "c", base_url=openrouter.DAHL_URL, provider_label="Dahl") == {"ok": True}
    assert seen["url"] == "https://inference.dahl.global/v1/chat/completions"


def test_error_messages_use_provider_label(monkeypatch):
    import intercom_exporter.openrouter as openrouter

    class FakeErrorResponse:
        status_code = 400

        def raise_for_status(self):
            import requests
            raise requests.HTTPError(response=self)

        def json(self):
            return {"error": {"message": "bad model"}}

    monkeypatch.setattr(openrouter.requests, "post", lambda *a, **k: FakeErrorResponse())
    with pytest.raises(openrouter.OpenRouterError, match="Dahl"):
        openrouter._request_result("k", "m", "p", "c", base_url=openrouter.DAHL_URL, provider_label="Dahl")


def test_standard_analysis_classifies_multiple_batches_and_synthesizes_once(tmp_path, monkeypatch):
    from intercom_exporter import openrouter
    from intercom_exporter.support_analysis import prepare_analysis

    rows = [dict(sample_rows()[0], id=f"conversation-{index}") for index in range(3)]
    source = tmp_path / "conversations.json"
    source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    prepared = prepare_analysis(source)
    monkeypatch.setattr(openrouter, "MAX_BATCH_INPUT_BYTES", 250)
    source_by_id = {row["id"]: row for row in rows}
    calls = []

    def fake_request(api_key, model, prompt, user_content, **_kwargs):
        calls.append(prompt)
        if prompt.startswith("Ты готовишь итоговый синтез"):
            return {"executive_summary": "Итог по трём обращениям.", "recommendations": ["Проверить подключение."]}
        batch_input = json.loads(user_content)
        batch_rows = [source_by_id[item["id"]] for item in batch_input["conversations"]]
        return valid_result(batch_rows)

    monkeypatch.setattr(openrouter, "_request_result", fake_request)

    result = run_standard_analysis(source, prepared["output_dir"], "test-secret", "test/model")

    assert len(calls) == 4
    final = json.loads((Path(prepared["output_dir"]) / "chatgpt_result.json").read_text(encoding="utf-8"))
    assert final["complete"] is True
    assert final["conversation_count"] == 3
    assert len(final["classifications"]) == 3
    assert final["executive_summary"] == "Итог по трём обращениям."
    assert Path(result["report_path"]).is_file()


def test_batch_with_unknown_id_is_retried_with_explicit_allowlist(tmp_path, monkeypatch):
    from intercom_exporter import openrouter
    from intercom_exporter.support_analysis import prepare_analysis

    rows = sample_rows()
    source = tmp_path / "conversations.json"
    source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    prepared = prepare_analysis(source)
    calls = []

    def fake_request(api_key, model, prompt, user_content, **_kwargs):
        calls.append((prompt, user_content))
        if len(calls) == 1:
            result = valid_result(rows)
            result["classifications"][0]["id"] = "215476545814049"
            return result
        return valid_result(rows)

    monkeypatch.setattr(openrouter, "_request_result", fake_request)
    run_standard_analysis(source, prepared["output_dir"], "test-secret", "test/model")

    assert len(calls) == 2
    assert '"id":"conversation-1"' in calls[0][1]
    assert "conversation-1" in calls[1][0]
    assert "215476545814049" in calls[1][0]


def test_standard_analysis_runs_batches_with_bounded_parallelism(tmp_path, monkeypatch):
    from intercom_exporter import openrouter
    from intercom_exporter.support_analysis import prepare_analysis

    rows = [dict(sample_rows()[0], id=f"conversation-{index}") for index in range(4)]
    source = tmp_path / "conversations.json"
    source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    prepared = prepare_analysis(source)
    monkeypatch.setattr(openrouter, "MAX_BATCH_INPUT_BYTES", 1)
    source_by_id = {row["id"]: row for row in rows}
    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_request(api_key, model, prompt, user_content, **_kwargs):
        nonlocal active, max_active
        if prompt.startswith("Ты готовишь итоговый синтез"):
            return {"executive_summary": "Итог.", "recommendations": ["Проверить подключение."]}
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.03)
            batch_rows = [source_by_id[item["id"]] for item in json.loads(user_content)["conversations"]]
            return valid_result(batch_rows)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(openrouter, "_request_result", fake_request)
    run_standard_analysis(source, prepared["output_dir"], "test-secret", "test/model", workers=2)

    assert max_active == 2


def test_verified_batch_cache_skips_api_request_on_repeat(tmp_path, monkeypatch):
    from intercom_exporter import openrouter
    from intercom_exporter.support_analysis import prepare_analysis

    rows = sample_rows()
    source = tmp_path / "conversations.json"
    source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    prepared = prepare_analysis(source)
    calls = []
    monkeypatch.setattr(openrouter, "_request_result", lambda *args, **_kwargs: calls.append(args) or valid_result(rows))

    run_standard_analysis(source, prepared["output_dir"], "test-secret", "test/model")
    run_standard_analysis(source, prepared["output_dir"], "test-secret", "test/model")

    assert len(calls) == 1


def test_empty_model_response_includes_safe_diagnostics():
    from intercom_exporter.openrouter import _decode_model_result

    body = {
        "id": "gen-test",
        "model": "vendor/model",
        "usage": {"completion_tokens": 100},
        "choices": [{"finish_reason": "length", "message": {"content": ""}}],
    }

    with pytest.raises(OpenRouterError, match="finish_reason=length.*completion_tokens=100.*gen-test"):
        _decode_model_result(body)


def test_transient_rate_limit_response_is_retried_with_retry_after(monkeypatch):
    from intercom_exporter import openrouter

    class RateLimitedResponse(FakeResponse):
        status_code = 429
        headers = {"Retry-After": "0"}

        def raise_for_status(self):
            import requests
            raise requests.HTTPError("limited", response=self)

    attempts = []
    monkeypatch.setattr(openrouter.time, "sleep", lambda _: None)

    def fake_post(*args, **kwargs):
        attempts.append(None)
        if len(attempts) == 1:
            return RateLimitedResponse({"error": {"message": "rate limited"}})
        return FakeResponse({"choices": [{"message": {"content": '{"ok":true}'}}]})

    monkeypatch.setattr(openrouter.requests, "post", fake_post)
    assert openrouter._request_result("test-key", "test/model", "prompt", "content") == {"ok": True}
    assert len(attempts) == 2


def test_monitoring_analysis_batches_union_and_generates_period_summaries(tmp_path, monkeypatch):
    from intercom_exporter import openrouter
    from intercom_exporter.support_analysis import prepare_monitoring_analysis

    rows = [dict(sample_rows()[0], id=f"conversation-{index}") for index in range(2)]
    week_dir, day_dir = tmp_path / "last_week", tmp_path / "last_day"
    week_dir.mkdir()
    day_dir.mkdir()
    week_path, day_path = week_dir / "conversations.json", day_dir / "conversations.json"
    week_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    day_path.write_text(json.dumps(rows[:1], ensure_ascii=False), encoding="utf-8")
    prepared = prepare_monitoring_analysis(week_path, day_path)
    monkeypatch.setattr(openrouter, "MAX_BATCH_INPUT_BYTES", 250)
    source_by_id = {row["id"]: row for row in rows}
    calls = []

    def fake_request(api_key, model, prompt, user_content, **_kwargs):
        calls.append(prompt)
        if prompt.startswith("Ты готовишь итоговый синтез"):
            return {
                "executive_summary": "Итог недели и дня.",
                "recommendations": ["Проверить подключение."],
                "period_analysis": {
                    "week": {"executive_summary": "Сводка недели.", "recommendations": []},
                    "day": {"executive_summary": "Сводка дня.", "recommendations": []},
                },
            }
        batch_input = json.loads(user_content)
        batch_rows = [source_by_id[item["id"]] for item in batch_input["conversations"]]
        result = valid_result(batch_rows)
        result["period_analysis"] = {}
        for name in ("week", "day"):
            selected = [row for row in batch_rows if name in batch_input["periods_by_id"][row["id"]]]
            payload = _build_payload(selected)
            result["period_analysis"][name] = {
                "conversation_count": len(selected),
                "dataset_fingerprint": payload["dataset_fingerprint"],
                "executive_summary": f"Промежуточная сводка {name}.",
                "recommendations": [],
            }
        return result

    monkeypatch.setattr(openrouter, "_request_result", fake_request)

    result = run_monitoring_analysis(week_path, day_path, prepared["output_dir"], "test-secret", "test/model")

    assert len(calls) == 3
    final = json.loads((Path(prepared["output_dir"]) / "chatgpt_result.json").read_text(encoding="utf-8"))
    assert final["conversation_count"] == 2
    assert len(final["classifications"]) == 2
    assert final["period_analysis"]["week"]["conversation_count"] == 2
    assert final["period_analysis"]["day"]["conversation_count"] == 1
    assert Path(result["report_path"]).is_file()


def test_api_failure_keeps_manual_files_and_does_not_create_reports(tmp_path, monkeypatch):
    from intercom_exporter.support_analysis import prepare_analysis
    from intercom_exporter import openrouter

    source = tmp_path / "conversations.json"
    source.write_text(json.dumps(sample_rows(), ensure_ascii=False), encoding="utf-8")
    prepared = prepare_analysis(source)

    def fail_post(*args, **kwargs):
        raise openrouter.requests.Timeout("timed out")

    monkeypatch.setattr(openrouter.requests, "post", fail_post)
    with pytest.raises(OpenRouterError, match="OpenRouter"):
        run_standard_analysis(source, prepared["output_dir"], "test-secret", "test/model")

    output = Path(prepared["output_dir"])
    assert (output / "chatgpt_input.json").is_file()
    assert (output / "PROMPT.md").is_file()
    assert not (output / "report.md").exists()
    assert not (output / "classifications.csv").exists()


@pytest.mark.parametrize("content", ["not json", "```json\n{}\n```\n"])
def test_invalid_or_incomplete_model_response_never_creates_report(tmp_path, monkeypatch, content):
    from intercom_exporter.support_analysis import prepare_analysis
    from intercom_exporter import openrouter

    source = tmp_path / "conversations.json"
    source.write_text(json.dumps(sample_rows(), ensure_ascii=False), encoding="utf-8")
    prepared = prepare_analysis(source)
    monkeypatch.setattr(openrouter.requests, "post", lambda *args, **kwargs: FakeResponse(
        {"choices": [{"message": {"content": content}}]}
    ))

    with pytest.raises(OpenRouterError):
        run_standard_analysis(source, prepared["output_dir"], "test-secret", "test/model")

    output = Path(prepared["output_dir"])
    assert not (output / "report.md").exists()
    assert not (output / "classifications.csv").exists()


def test_monitoring_analysis_makes_one_request_and_writes_shared_outputs(tmp_path, monkeypatch):
    from intercom_exporter.support_analysis import prepare_monitoring_analysis
    from intercom_exporter import openrouter

    week_dir, day_dir = tmp_path / "last_week", tmp_path / "last_day"
    week_dir.mkdir()
    day_dir.mkdir()
    rows = sample_rows()
    week_path, day_path = week_dir / "conversations.json", day_dir / "conversations.json"
    week_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    day_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    prepared = prepare_monitoring_analysis(week_path, day_path)
    combined_result = valid_result(rows)
    week_payload = _build_payload(rows)
    combined_result["period_analysis"] = {
        "week": {"conversation_count": 1, "dataset_fingerprint": week_payload["dataset_fingerprint"], "executive_summary": "Резюме недели.", "recommendations": []},
        "day": {"conversation_count": 1, "dataset_fingerprint": week_payload["dataset_fingerprint"], "executive_summary": "Резюме дня.", "recommendations": []},
    }
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse({"choices": [{"message": {"content": json.dumps(combined_result, ensure_ascii=False)}}]})

    monkeypatch.setattr(openrouter.requests, "post", fake_post)
    result = run_monitoring_analysis(week_path, day_path, prepared["output_dir"], "test-secret", "test/model")

    assert len(calls) == 1
    output = Path(prepared["output_dir"])
    assert (output / "chatgpt_result.json").is_file()
    assert (output / "report.md").is_file()
    assert (output / "classifications.csv").is_file()
    assert result["unique_conversation_count"] == 1

import json
from pathlib import Path

import pytest

from intercom_exporter.agents_orchestration import merge_agents_results, prepare_agents_bundle
from intercom_exporter.support_analysis import AnalysisError, OTHER_SUBCATEGORIES, main


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _setup(tmp_path, service=None):
    rows = [
        {"id": "login", "title": "", "source": {"body": "Не получается войти"}},
        {"id": "empty", "title": "", "source": {"body": "/start"}},
    ]
    week = tmp_path / "last_week" / "conversations.json"
    day = tmp_path / "last_day" / "conversations.json"
    _write(week, rows)
    _write(day, rows[:1])
    output = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, output, workers=2, service_id=service)
    for entry in manifest["chunks"]:
        chunk = json.loads(Path(entry["chunk_path"]).read_text(encoding="utf-8"))
        _write(Path(entry["result_path"]), {"classifications": [
            {"id": c["id"], "category": "Прочие обращения", "subcategory": "Неясный объект проблемы",
             "problem_summary": "Недостаточно информации", "severity": None,
             "severity_reason": "Не оценена", "confidence": "low", "evidence": ""}
            for c in chunk["conversations"]
        ]})
    return week, day, output


def _fill_review(pending, service=None):
    manifest = json.loads(Path(pending["review_manifest_path"]).read_text(encoding="utf-8"))
    for entry in manifest["chunks"]:
        chunk = json.loads(Path(entry["chunk_path"]).read_text(encoding="utf-8"))
        rows = []
        for c in chunk["conversations"]:
            login = c["id"] == "login"
            rows.append({
                "id": c["id"], "category": ("Авторизация и аккаунт" if service == "hypertube"
                                                       else "Авторизация и аккаунт") if login else "Прочие обращения",
                "subcategory": "Ошибка входа" if login else "Нет запроса",
                "problem_summary": "Клиент не может войти" if login else "Только команда запуска бота",
                "severity": None, "severity_reason": "Влияние не описано", "confidence": "medium" if login else "low",
                "evidence": "Не получается войти" if login else "/start", "platforms": [], "additional_topics": [],
                "review_reason": "Клиент прямо описал затруднение со входом" if login else "В полной переписке только /start, вопроса нет",
            })
        _write(Path(entry["result_path"]), {"review_fingerprint": chunk["review_fingerprint"], "classifications": rows})
    return manifest


def test_other_cases_require_review_before_report(tmp_path):
    week, day, output = _setup(tmp_path)
    pending = merge_agents_results(week, day, output)
    assert pending["review_required"] is True
    manifest = json.loads(Path(pending["review_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["unique_conversation_count"] == 2
    assert not (output / "report.md").exists()
    chunk = json.loads(Path(manifest["chunks"][0]["chunk_path"]).read_text(encoding="utf-8"))
    assert chunk["conversations"][0]["messages"][0]["text"] == "Не получается войти"
    assert chunk["previous_classifications"][0]["category"] == "Прочие обращения"


@pytest.mark.parametrize("service", [None, "hypertube"])
def test_review_reclassifies_clear_request_and_explains_remaining_other(tmp_path, service):
    week, day, output = _setup(tmp_path, service)
    pending = merge_agents_results(week, day, output, service_id=service)
    _fill_review(pending, service)
    outcome = merge_agents_results(week, day, output, service_id=service)
    result = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    assert result["classifications"][0]["subcategory"] == "Ошибка входа"
    assert result["classifications"][1]["subcategory"] == "Нет запроса"
    audit = json.loads(Path(outcome["other_review_audit_path"]).read_text(encoding="utf-8"))
    assert audit["before_count"] == 2 and audit["after_count"] == 1
    assert audit["decisions"][0]["review_reason"]
    assert "Что входит в «Прочие обращения»" in Path(outcome["report_path"]).read_text(encoding="utf-8")
    assert "Нет запроса" in Path(outcome["dashboard_path"]).read_text(encoding="utf-8")
    dashboard = json.loads(Path(outcome["dashboard_path"]).with_suffix(".json").read_text(encoding="utf-8"))
    assert dashboard["other_breakdown"] == {"week": {"Нет запроса": 1}, "day": {}}


@pytest.mark.parametrize("mutation,message", [
    (lambda value: value.update(review_fingerprint="old"), "отпечаток"),
    (lambda value: value["classifications"][0].update(id="alien"), "неизвестный ID"),
    (lambda value: value["classifications"].clear(), "пропущены"),
    (lambda value: value["classifications"].append(value["classifications"][0]), "повторяется"),
    (lambda value: value["classifications"][0].update(review_reason=""), "review_reason"),
    (lambda value: value["classifications"][0].update(evidence="выдуманная цитата"), "evidence"),
])
def test_review_rejects_invalid_or_stale_decisions(tmp_path, mutation, message):
    week, day, output = _setup(tmp_path)
    pending = merge_agents_results(week, day, output)
    manifest = _fill_review(pending)
    path = Path(manifest["chunks"][0]["result_path"])
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    _write(path, value)
    with pytest.raises(AnalysisError, match=message):
        merge_agents_results(week, day, output)
    assert not (output / "report.md").exists()


def test_cli_merge_returns_pending_without_slack(tmp_path, monkeypatch):
    week, day, output = _setup(tmp_path)
    monkeypatch.setattr("intercom_exporter.support_analysis._maybe_notify_slack", lambda *a: pytest.fail("Slack called"))
    assert main(["merge-agents", "--week", str(week), "--day", str(day), "--service", "vpn"]) == 3
    assert not (output / "report.md").exists()


@pytest.mark.parametrize("service", [None, "hypertube"])
def test_primary_prompt_restricts_other_and_supports_title_only_requests(tmp_path, service):
    _, _, output = _setup(tmp_path, service)
    prompt = (output / "PROMPT.md").read_text(encoding="utf-8")
    assert all(label in prompt for label in OTHER_SUBCATEGORIES)
    assert "Как поменять карту для оплаты?" in prompt
    assert "Не получается войти" in prompt


def test_review_does_not_reuse_decisions_when_source_changes(tmp_path):
    week, day, output = _setup(tmp_path)
    old = merge_agents_results(week, day, output)
    _fill_review(old)
    rows = json.loads(week.read_text(encoding="utf-8"))
    rows[1]["title"] = "Изменённый контекст"
    _write(week, rows)
    new = merge_agents_results(week, day, output)
    assert new["review_required"]
    assert new["review_manifest_path"] != old["review_manifest_path"]
    assert Path(old["review_manifest_path"]).exists()


def test_review_rejects_unexplained_other_subcategory(tmp_path):
    week, day, output = _setup(tmp_path)
    pending = merge_agents_results(week, day, output)
    manifest = _fill_review(pending)
    path = Path(manifest["chunks"][1]["result_path"])
    value = json.loads(path.read_text(encoding="utf-8"))
    value["classifications"][0]["subcategory"] = "Другое"
    _write(path, value)
    with pytest.raises(AnalysisError, match="конкретная причина"):
        merge_agents_results(week, day, output)


def test_research_waits_for_other_review_and_then_finishes(tmp_path, monkeypatch):
    week, day, output = _setup(tmp_path)
    monkeypatch.setattr("intercom_exporter.support_analysis._maybe_notify_slack", lambda *a: pytest.fail("Slack called"))
    args = ["research", "--run", str(tmp_path), "--service", "vpn", "--no-notify"]
    assert main(args) == 3
    pending = merge_agents_results(week, day, output)
    _fill_review(pending)
    monkeypatch.setattr("intercom_exporter.support_analysis._maybe_notify_slack", lambda *a: None)
    assert main(args) == 0
    assert (output / "report.md").is_file()


def test_title_only_request_is_available_to_review_and_valid_evidence(tmp_path):
    week, day, output = _setup(tmp_path)
    rows = json.loads(week.read_text(encoding="utf-8"))
    rows[0].update(title="Как поменять карту для оплаты?", source={"body": ""})
    _write(week, rows)
    _write(day, rows[:1])
    pending = merge_agents_results(week, day, output)
    manifest = _fill_review(pending)
    path = Path(manifest["chunks"][0]["result_path"])
    value = json.loads(path.read_text(encoding="utf-8"))
    value["classifications"][0].update(
        category="Оплата и подписка", subcategory="Смена платёжной карты",
        evidence=rows[0]["title"], review_reason="Содержательный запрос полностью указан в заголовке",
    )
    _write(path, value)
    outcome = merge_agents_results(week, day, output)
    result = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    assert result["classifications"][0]["subcategory"] == "Смена платёжной карты"

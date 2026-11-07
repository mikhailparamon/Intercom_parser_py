import pytest
import json
from pathlib import Path

from intercom_exporter.classification_catalog import (
    get_subcategories, canonical_classification, confirmed_platforms,
)


def test_service_catalogs_have_agreed_sizes_and_stable_codes():
    assert len(get_subcategories("vpn")) == 20
    assert len(get_subcategories("hypertube")) == 19
    for service in ("vpn", "hypertube"):
        items = get_subcategories(service)
        assert len({item["code"] for item in items}) == len(items)


def test_new_results_use_code_and_canonical_label_not_free_description():
    item = canonical_classification({"subcategory_code": "subscription_cancel", "subcategory": "Хочу отключить автопродление", "category": "Оплата и подписка"}, "vpn")
    assert item["subcategory"] == "Отмена подписки"
    with pytest.raises(ValueError, match="подкатегор"):
        canonical_classification({"subcategory_code": "invented", "category": "Оплата и подписка"}, "vpn")
    with pytest.raises(ValueError, match="категор"):
        canonical_classification({"subcategory_code": "subscription_cancel", "category": "Ключи и конфигурации"}, "vpn")


def test_platforms_are_recovered_and_deduplicated_from_customer_evidence():
    conversation = {"messages": [{"role": "customer", "text": "Через OpenVPN Connect на iPhone не подключается. OpenVPN."}]}
    assert set(confirmed_platforms(conversation, "vpn")) == {"OpenVPN", "iOS/iPadOS"}


def test_platforms_ignore_operator_menu_mail_signature_and_quoted_history():
    conversation = {"messages": [
        {"role": "operator", "text": "Какой тип подключения: OpenVPN, Chrome или наше приложение?"},
        {"role": "customer", "text": "Хочу возврат\n--\nОтправлено из Mail для Android"},
        {"role": "customer", "text": "Спасибо\n> Раньше использовал OpenVPN на Windows"},
    ]}
    assert confirmed_platforms(conversation, "vpn") == []


def test_platforms_keep_browser_os_and_app_together_without_guessing():
    conversation = {"messages": [{"role": "customer", "text": "В расширении Chrome на Windows не приходит код. На телефоне приложение VPN Наоборот работает."}]}
    assert set(confirmed_platforms(conversation, "vpn")) == {"Расширение браузера", "Chrome", "Windows", "Приложение VPN Наоборот"}


def test_forwarded_support_template_is_not_customer_platform_evidence():
    conversation = {"messages": [{"role": "customer", "text": "VPN Наоборот – Поддержка: Через минуту придёт ключ. Удалите старый ключ из OpenVPN Connect."}]}
    assert confirmed_platforms(conversation, "vpn") == []


def test_platforms_use_unknown_desktop_only_when_no_os_was_confirmed():
    assert confirmed_platforms({"messages": [{"role": "customer", "text": "На компьютере не работает"}]}, "hypertube") == ["Компьютер (ОС не указана)"]


def test_strict_bundle_merge_csv_and_dashboard_share_canonical_values(tmp_path):
    from intercom_exporter.agents_orchestration import prepare_agents_bundle, merge_agents_results
    from intercom_exporter.support_analysis import AnalysisError

    week, day = tmp_path / "last_week/conversations.json", tmp_path / "last_day/conversations.json"
    rows = [{"id": "a", "source": {"body": "На iPhone через OpenVPN не подключается"}}, {"id": "b", "source": {"body": "VPN не подключается"}}]
    for path, values in ((week, rows), (day, rows[:1])):
        path.parent.mkdir()
        path.write_text(json.dumps(values), encoding="utf-8")
    output = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, output, workers=2)
    assert manifest["classification_schema_version"] == 2
    assert len(json.loads((output / "agents_work/taxonomy.json").read_text(encoding="utf-8"))["subcategories"]) == 20
    for entry in manifest["chunks"]:
        chunk = json.loads(Path(entry["chunk_path"]).read_text(encoding="utf-8"))
        items = [{"id": c["id"], "category": "Проблемы подключения VPN", "subcategory": "Перестал коннектиться", "subcategory_code": "invented", "platforms": [], "additional_topics": [], "problem_summary": "Не подключается", "severity": None, "severity_reason": "Не описано влияние", "confidence": "high", "evidence": "не подключается"} for c in chunk["conversations"]]
        Path(entry["result_path"]).write_text(json.dumps({"classifications": items}), encoding="utf-8")
    with pytest.raises(AnalysisError, match="код подкатегории"):
        merge_agents_results(week, day, output)
    assert not (output / "report.md").exists()
    for entry in manifest["chunks"]:
        path = Path(entry["result_path"])
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data["classifications"]:
            item["subcategory_code"] = "vpn_connection"
        path.write_text(json.dumps(data), encoding="utf-8")
    outcome = merge_agents_results(week, day, output)
    result = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    assert {r["subcategory"] for r in result["classifications"]} == {"VPN не подключается"}
    assert set(result["classifications"][0]["platforms"]) == {"OpenVPN", "iOS/iPadOS"}
    assert "subcategory_code" in Path(outcome["classifications_path"]).read_text(encoding="utf-8-sig").splitlines()[0]
    dashboard = json.loads(Path(outcome["dashboard_path"]).with_suffix(".json").read_text(encoding="utf-8"))
    assert dashboard["subcategory_coverage"]["in_top"] == 2
    assert dashboard["platform_coverage"]["without_platform"] == 1


def test_strict_report_rejects_invalid_code_but_legacy_result_still_loads():
    from intercom_exporter.support_analysis import _validate_result, AnalysisError

    rows = [{"id": "a"}]
    item = {"id": "a", "category": "Оплата и подписка", "subcategory": "Свободное старое название", "problem_summary": "Описание", "severity": None, "severity_reason": "Не оценена", "confidence": "low", "evidence": ""}
    result = {"complete": True, "conversation_count": 1, "dataset_fingerprint": "fp", "categories": [{"name": "Оплата и подписка", "description": "Оплата"}], "classifications": [item], "executive_summary": "Итог", "recommendations": []}
    assert _validate_result(rows, {"dataset_fingerprint": "fp"}, result)[1][0]["subcategory"] == "Свободное старое название"
    result.update(classification_schema_version=2, service_id="vpn")
    with pytest.raises(AnalysisError, match="код подкатегории"):
        _validate_result(rows, {"dataset_fingerprint": "fp"}, result)


@pytest.mark.parametrize("service", ["vpn", "hypertube"])
def test_strict_other_review_preserves_codes_and_checks_category_pairs(tmp_path, service):
    from intercom_exporter.agents_orchestration import prepare_agents_bundle, merge_agents_results

    rows = [{"id": "login", "source": {"body": "Не получается войти"}}, {"id": "empty", "source": {"body": "/start"}}]
    week, day = tmp_path / "last_week/conversations.json", tmp_path / "last_day/conversations.json"
    for path, values in ((week, rows), (day, rows[:1])):
        path.parent.mkdir()
        path.write_text(json.dumps(values), encoding="utf-8")
    output = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, output, workers=2, service_id=service)
    def item(cid, code, category, evidence):
        return {"id": cid, "subcategory_code": code, "subcategory": "Будет заменено кодом", "category": category, "platforms": [], "additional_topics": [], "problem_summary": "Описание", "confidence": "low", "severity": None, "severity_reason": "Влияние не описано", "evidence": evidence, "review_reason": "Проверена полная переписка"}
    for entry in manifest["chunks"]:
        chunk = json.loads(Path(entry["chunk_path"]).read_text(encoding="utf-8"))
        Path(entry["result_path"]).write_text(json.dumps({"classifications": [item(c["id"], "no_request", "Прочие обращения", "") for c in chunk["conversations"]]}), encoding="utf-8")
    pending = merge_agents_results(week, day, output, service_id=service)
    review = json.loads(Path(pending["review_manifest_path"]).read_text(encoding="utf-8"))
    for entry in review["chunks"]:
        chunk = json.loads(Path(entry["chunk_path"]).read_text(encoding="utf-8"))
        classifications = [item(c["id"], "login_error" if c["id"] == "login" else "no_request", "Авторизация и аккаунт" if c["id"] == "login" else "Прочие обращения", "Не получается войти" if c["id"] == "login" else "/start") for c in chunk["conversations"]]
        Path(entry["result_path"]).write_text(json.dumps({"review_fingerprint": chunk["review_fingerprint"], "classifications": classifications}), encoding="utf-8")
    outcome = merge_agents_results(week, day, output, service_id=service)
    data = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    assert [c["subcategory_code"] for c in data["classifications"]] == ["login_error", "no_request"]
    assert data["classifications"][0]["subcategory"] == "Ошибка входа или аккаунта"
    assert "Полное распределение подкатегорий" in Path(outcome["report_path"]).read_text(encoding="utf-8")

import json
from pathlib import Path

import pytest

from intercom_exporter.agents_orchestration import (
    TAXONOMY,
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

    manifest = prepare_agents_bundle(week, day, out, workers=5)

    work = out / "agents_work"
    assert (work / "taxonomy.json").is_file()
    assert (work / "manifest.json").is_file()
    assert (work / "agents_protocol.md").is_file()
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
    manifest = prepare_agents_bundle(week, day, out, workers=3)
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
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_results(manifest, category=None, skip_worker=2)

    with pytest.raises(AnalysisError, match="воркер 2"):
        merge_agents_results(week, day, out)


def test_merge_agents_results_rejects_unknown_category(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_results(manifest, category="Выдуманная категория")

    with pytest.raises(AnalysisError, match="не из таксономии"):
        merge_agents_results(week, day, out)


def test_merge_agents_results_compare_with_previous(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=2)
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
    manifest = prepare_agents_bundle(week, day, out, workers=2)
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
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_results(manifest, category=None)
    entry = manifest["chunks"][0]
    Path(entry["result_path"]).write_text(json.dumps({"classifications": {"a": "x"}}, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AnalysisError, match="не является списком"):
        merge_agents_results(week, day, out)


def test_merge_agents_results_rejects_non_dict_item(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=2)
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

    manifest = prepare_agents_bundle(week, day, out, workers=20)

    assert manifest["workers"] == 3
    assert len(manifest["chunks"]) == 3


def test_prepare_agents_bundle_generates_prompt(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"

    manifest = prepare_agents_bundle(week, day, out, workers=2)

    prompt_path = out / "PROMPT.md"
    assert prompt_path.is_file()
    assert manifest["prompt_path"] == str(prompt_path)
    assert "таксоном" in prompt_path.read_text(encoding="utf-8").lower()

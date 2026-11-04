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
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_results(manifest, category=None)

    code = main(["merge-agents", "--week", str(week), "--day", str(day)])
    assert code == 0
    assert "Дашборд" in capsys.readouterr().out
    assert (out / "dashboard.html").is_file()


def test_merge_agents_results_uses_recommendations_file(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=2)
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
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_results(manifest, category=None)
    rec_path = tmp_path / "recommendations.json"
    rec_path.write_text(json.dumps({"not": "a list"}, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AnalysisError, match="списком непустых строк"):
        merge_agents_results(week, day, out, recommendations=rec_path)


def test_merge_agents_results_accepts_recommendations_list(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=2)
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
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_results(manifest, category=None)
    monkeypatch.chdir(tmp_path)

    code = main(["merge-agents"])

    assert code == 0
    assert "Дашборд" in capsys.readouterr().out
    assert (out / "dashboard.html").is_file()


def test_cli_merge_agents_picks_up_agents_work_recommendations(tmp_path, monkeypatch):
    from intercom_exporter.support_analysis import main

    run = tmp_path / "data" / "run1"
    week = _write_conversations(run / "last_week", ["a"])
    day = _write_conversations(run / "last_day", ["a"])
    out = run / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=2)
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
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_results(manifest, category=None)
    monkeypatch.chdir(tmp_path)
    assert main(["merge-agents"]) == 0
    (out / "dashboard.html").unlink()

    code = main(["render-dashboard", "--dashboard-dir", str(tmp_path / "dash")])

    assert code == 0
    assert (tmp_path / "dash" / "dashboard.html").is_file()


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
    manifest = prepare_agents_bundle(week, day, out, workers=2)
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
    manifest = prepare_agents_bundle(week, day, out, workers=2)
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
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_results(manifest, category=None)
    (out / "agents_work" / "recommendations.json").write_text(
        json.dumps(["Совет"], ensure_ascii=False), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code = main(["research", "--dashboard-dir", str(tmp_path / "dash")])

    assert code == 0
    assert (tmp_path / "dash" / "dashboard.html").is_file()
    result = json.loads((out / "chatgpt_result.json").read_text(encoding="utf-8"))
    assert result["recommendations"] == ["Совет"]

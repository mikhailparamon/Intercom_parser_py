import json
from pathlib import Path

import pytest

from intercom_exporter.cli import main
from intercom_exporter.config import load_config


def test_config_reads_token_and_api_version_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("INTERCOM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("INTERCOM_API_VERSION", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("INTERCOM_ACCESS_TOKEN=fake-token\nINTERCOM_API_VERSION=2.17\n", encoding="utf-8")
    config = load_config(env_file)
    assert config.access_token == "fake-token"
    assert config.api_version == "2.17"


def test_config_missing_token_has_setup_guidance(tmp_path, monkeypatch):
    monkeypatch.delenv("INTERCOM_ACCESS_TOKEN", raising=False)
    with pytest.raises(ValueError, match="INTERCOM_ACCESS_TOKEN"):
        load_config(tmp_path / "missing.env")


def test_dry_run_needs_no_token_and_makes_no_api_calls(capsys):
    assert main(["--dry-run", "--start", "2026-07-01", "--end", "2026-10-01", "--workers", "6"]) == 0
    output = capsys.readouterr().out
    assert "2026-07-01" in output
    assert "all" in output
    assert "important" in output


def test_dry_run_shows_explicit_selection_and_fields(capsys):
    assert main(["--dry-run", "--start", "2026-07-01", "--end", "2026-10-01",
                 "--selection", "updated", "--fields", "all"]) == 0
    output = capsys.readouterr().out
    assert "updated" in output
    assert "all" in output


def test_cli_rejects_unknown_selection_or_fields():
    with pytest.raises(SystemExit) as selection_error:
        main(["--dry-run", "--selection", "recent"])
    assert selection_error.value.code == 2
    with pytest.raises(SystemExit) as fields_error:
        main(["--dry-run", "--fields", "minimal"])
    assert fields_error.value.code == 2


def test_cli_rejects_nonpositive_worker_count():
    with pytest.raises(SystemExit) as error:
        main(["--dry-run", "--start", "2026-07-01", "--end", "2026-10-01", "--workers", "0"])
    assert error.value.code == 2


def test_cli_rejects_only_one_period_date():
    with pytest.raises(SystemExit) as error:
        main(["--dry-run", "--start", "2026-07-01"])
    assert error.value.code == 2


def test_cli_prepares_analysis_for_fresh_successful_export(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    import intercom_exporter.cli as cli

    output_dir = tmp_path / "2026-07-01_2026-10-01"
    calls = []

    class FakeExporter:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            return {"status": "success", "found_count": 3, "exported_count": 3,
                    "error_count": 0, "elapsed_seconds": 1, "output_dir": str(output_dir)}

    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(access_token="token", api_version="2.17"))
    monkeypatch.setattr(cli, "IntercomClient", lambda *args: object())
    monkeypatch.setattr(cli, "ConversationExporter", FakeExporter)
    monkeypatch.setattr(cli, "prepare_analysis", lambda path: (
        calls.append(Path(path)) or {"conversation_count": 3, "output_dir": str(output_dir / "support-analysis")}
    ))

    assert cli.main(["--start", "2026-07-01", "--end", "2026-10-01", "--output", str(tmp_path)]) == 0

    assert calls == [output_dir / "conversations.jsonl"]
    assert "Подготовка данных для ChatGPT" in capsys.readouterr().out


def test_cli_analyzes_exported_rows_from_partial_export_and_allows_opt_out(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    import intercom_exporter.cli as cli

    output_dir = tmp_path / "2026-07-01_2026-10-01"
    calls = []
    export_status = {"status": "partial"}

    class FakeExporter:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            return {**export_status, "found_count": 3, "exported_count": 2,
                    "error_count": 1, "elapsed_seconds": 1, "output_dir": str(output_dir)}

    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(access_token="token", api_version="2.17"))
    monkeypatch.setattr(cli, "IntercomClient", lambda *args: object())
    monkeypatch.setattr(cli, "ConversationExporter", FakeExporter)
    monkeypatch.setattr(cli, "prepare_analysis", lambda path: (
        calls.append(Path(path)) or {"conversation_count": 2, "output_dir": str(output_dir / "support-analysis")}
    ))

    assert cli.main(["--start", "2026-07-01", "--end", "2026-10-01", "--output", str(tmp_path)]) == 1
    assert calls == [output_dir / "conversations.jsonl"]
    partial_output = capsys.readouterr().out.lower()
    assert "подготовка данных для chatgpt" in partial_output
    assert "экспорт частичный" in partial_output
    assert "обращений: 2" in partial_output

    export_status["status"] = "success"
    calls.clear()
    assert cli.main(["--start", "2026-07-01", "--end", "2026-10-01", "--output", str(tmp_path),
                     "--skip-analysis"]) == 0
    assert calls == []


def test_logs_and_manifest_do_not_expose_api_token(tmp_path, caplog):
    from datetime import date
    from intercom_exporter.dates import DateRange
    from intercom_exporter.exporter import ConversationExporter

    class BadClient:
        def search_conversations(self, start, end):
            yield {"id": "1", "created_at": DateRange(start, end).epoch_bounds()[0]}
        def get_conversation(self, conversation_id):
            raise RuntimeError("fake-secret")

    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    ConversationExporter(BadClient()).run(period, tmp_path)
    assert "fake-secret" not in caplog.text
    manifest = json.loads((tmp_path / "2026-07-01_2026-10-01" / "manifest.json").read_text(encoding="utf-8"))
    assert "fake-secret" not in json.dumps(manifest)

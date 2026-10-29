import json

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

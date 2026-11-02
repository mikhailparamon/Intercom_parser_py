import json
from datetime import date
from pathlib import Path

import pytest

from intercom_exporter.cli import main
import intercom_exporter.cli as cli
from intercom_exporter.config import load_config, load_openrouter_settings


def test_config_reads_token_and_api_version_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("INTERCOM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("INTERCOM_API_VERSION", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("INTERCOM_ACCESS_TOKEN=fake-token\nINTERCOM_API_VERSION=2.17\n", encoding="utf-8")
    config = load_config(env_file)
    assert config.access_token == "fake-token"
    assert config.api_version == "2.17"


def test_openrouter_settings_are_optional_and_read_model_override(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    assert load_openrouter_settings(tmp_path / "missing.env") is None
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=router-key\nOPENROUTER_MODEL=vendor/model\n", encoding="utf-8")
    settings = load_openrouter_settings(env_file)
    assert settings.api_key == "router-key"
    assert settings.model == "vendor/model"
    assert settings.workers == 3


def test_openrouter_workers_can_be_configured_and_must_be_positive(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_WORKERS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=router-key\nOPENROUTER_WORKERS=5\n", encoding="utf-8")
    assert load_openrouter_settings(env_file).workers == 5

    env_file.write_text("OPENROUTER_API_KEY=router-key\nOPENROUTER_WORKERS=0\n", encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_WORKERS", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_WORKERS"):
        load_openrouter_settings(env_file)


def test_config_missing_token_has_setup_guidance(tmp_path, monkeypatch):
    monkeypatch.delenv("INTERCOM_ACCESS_TOKEN", raising=False)
    with pytest.raises(ValueError, match="INTERCOM_ACCESS_TOKEN"):
        load_config(tmp_path / "missing.env")


def test_dry_run_needs_no_token_and_makes_no_api_calls(capsys):
    assert main(["--standard", "--dry-run", "--start", "2026-07-01", "--end", "2026-10-01", "--workers", "6"]) == 0
    output = capsys.readouterr().out
    assert "2026-07-01" in output
    assert "all" in output
    assert "important" in output


def test_dry_run_shows_explicit_selection_and_fields(capsys):
    assert main(["--standard", "--dry-run", "--start", "2026-07-01", "--end", "2026-10-01",
                 "--selection", "updated", "--fields", "all", "--timezone", "Europe/Moscow"]) == 0
    output = capsys.readouterr().out
    assert "updated" in output
    assert "all" in output
    assert "Europe/Moscow" in output


def test_cli_rejects_unknown_timezone(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--dry-run", "--timezone", "Not/A_Timezone"])
    assert error.value.code == 2
    assert "Неизвестный часовой пояс" in capsys.readouterr().err


def test_cli_rejects_unknown_selection_or_fields():
    with pytest.raises(SystemExit) as selection_error:
        main(["--dry-run", "--selection", "recent"])
    assert selection_error.value.code == 2
    with pytest.raises(SystemExit) as fields_error:
        main(["--dry-run", "--fields", "minimal"])
    assert fields_error.value.code == 2


def test_cli_rejects_nonpositive_worker_count():
    with pytest.raises(SystemExit) as error:
        main(["--standard", "--dry-run", "--start", "2026-07-01", "--end", "2026-10-01", "--workers", "0"])
    assert error.value.code == 2


def test_cli_rejects_only_one_period_date():
    with pytest.raises(SystemExit) as error:
        main(["--standard", "--dry-run", "--start", "2026-07-01"])
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

    assert cli.main(["--standard", "--start", "2026-07-01", "--end", "2026-10-01", "--output", str(tmp_path)]) == 0

    assert calls == [output_dir / "conversations.json"]
    assert "Подготовка данных для ChatGPT" in capsys.readouterr().out


def test_standard_cli_runs_openrouter_after_preparing_inputs_when_key_is_configured(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    output_dir = tmp_path / "2026-07-01_2026-10-01"
    calls = []

    class FakeExporter:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            return {"status": "success", "found_count": 1, "exported_count": 1,
                    "error_count": 0, "elapsed_seconds": 1, "output_dir": str(output_dir)}

    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(
        access_token="token", api_version="2.17", openrouter_api_key="router-key", openrouter_model="vendor/model",
    ))
    monkeypatch.setattr(cli, "IntercomClient", lambda *args: object())
    monkeypatch.setattr(cli, "ConversationExporter", FakeExporter)
    monkeypatch.setattr(cli, "prepare_analysis", lambda path: {
        "conversation_count": 1, "output_dir": str(output_dir / "support-analysis"),
    })
    monkeypatch.setattr(cli, "run_standard_analysis", lambda *args: (
        calls.append(args) or {"result_path": "result", "report_path": "report", "classifications_path": "csv"}
    ))

    assert cli.main(["--standard", "--start", "2026-07-01", "--end", "2026-10-01"]) == 0
    assert calls == [(output_dir / "conversations.json", str(output_dir / "support-analysis"), "router-key", "vendor/model", 3)]
    assert "Анализ завершён через OpenRouter" in capsys.readouterr().out


def test_monitoring_cli_runs_one_openrouter_analysis_for_week_and_day(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    week_dir, day_dir, analysis_dir = tmp_path / "last_week", tmp_path / "last_day", tmp_path / "support-analysis"
    calls = []
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(
        access_token="token", api_version="2.17", openrouter_api_key="router-key", openrouter_model="vendor/model",
    ))
    monkeypatch.setattr(cli, "IntercomClient", lambda *args: object())
    monkeypatch.setattr(cli, "ConversationExporter", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "run_monitoring", lambda *args, **kwargs: {
        "export": {"status": "success", "found_count": 1, "exported_count": 1, "error_count": 0, "elapsed_seconds": 1},
        "snapshots": {
            "last_week": {"conversation_count": 1, "output_dir": str(week_dir)},
            "last_day": {"conversation_count": 1, "output_dir": str(day_dir)},
        },
        "analysis": {"output_dir": str(analysis_dir)},
    })
    monkeypatch.setattr(cli, "run_monitoring_analysis", lambda *args: (
        calls.append(args) or {"result_path": "result", "report_path": "report", "classifications_path": "csv"}
    ))

    assert cli.main([]) == 0

    assert calls == [(week_dir / "conversations.json", day_dir / "conversations.json", str(analysis_dir), "router-key", "vendor/model", 3)]
    assert "Анализ завершён через OpenRouter" in capsys.readouterr().out


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

    assert cli.main(["--standard", "--start", "2026-07-01", "--end", "2026-10-01", "--output", str(tmp_path)]) == 1
    assert calls == [output_dir / "conversations.json"]
    partial_output = capsys.readouterr().out.lower()
    assert "подготовка данных для chatgpt" in partial_output
    assert "экспорт частичный" in partial_output
    assert "обращений: 2" in partial_output

    export_status["status"] = "success"
    calls.clear()
    assert cli.main(["--standard", "--start", "2026-07-01", "--end", "2026-10-01", "--output", str(tmp_path),
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


def test_cli_defaults_to_monitoring_and_dry_run_shows_rolling_windows(monkeypatch, capsys):
    from datetime import datetime, timezone
    from intercom_exporter.dates import DateTimeRange

    monkeypatch.setattr(cli, "build_monitoring_windows", lambda: {
        "backup_3_months": DateTimeRange(datetime(2026, 7, 31, tzinfo=timezone.utc), datetime(2026, 10, 31, tzinfo=timezone.utc)),
        "last_week": DateTimeRange(datetime(2026, 10, 24, tzinfo=timezone.utc), datetime(2026, 10, 31, tzinfo=timezone.utc)),
        "last_day": DateTimeRange(datetime(2026, 10, 30, tzinfo=timezone.utc), datetime(2026, 10, 31, tzinfo=timezone.utc)),
    })

    assert cli.main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Мониторинг UTC" in output
    assert "архив выключен" in output
    assert "2026-10-24" in output
    assert "2026-10-30" in output
    assert "2026-07-31" not in output


def test_cli_dry_run_with_full_archive_shows_archive_window(monkeypatch, capsys):
    from datetime import datetime, timezone
    from intercom_exporter.dates import DateTimeRange

    monkeypatch.setattr(cli, "build_monitoring_windows", lambda: {
        "backup_3_months": DateTimeRange(datetime(2026, 7, 31, tzinfo=timezone.utc), datetime(2026, 10, 31, tzinfo=timezone.utc)),
        "last_week": DateTimeRange(datetime(2026, 10, 24, tzinfo=timezone.utc), datetime(2026, 10, 31, tzinfo=timezone.utc)),
        "last_day": DateTimeRange(datetime(2026, 10, 30, tzinfo=timezone.utc), datetime(2026, 10, 31, tzinfo=timezone.utc)),
    })

    assert cli.main(["--dry-run", "--full-archive"]) == 0
    output = capsys.readouterr().out
    assert "архив [2026-07-31" in output
    assert "2026-10-24" in output


def test_cli_monitoring_passes_full_archive_flag(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    captured = {}
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(access_token="token", api_version="2.17"))
    monkeypatch.setattr(cli, "IntercomClient", lambda *args: object())
    monkeypatch.setattr(cli, "ConversationExporter", lambda *args, **kwargs: object())

    def fake_run_monitoring(*args, **kwargs):
        captured.update(kwargs)
        return {
            "export": {"status": "success", "found_count": 0, "exported_count": 0, "error_count": 0, "elapsed_seconds": 0},
            "snapshots": {
                "backup_3_months": {"conversation_count": 0, "output_dir": str(tmp_path / "backup")},
                "last_week": {"conversation_count": 0, "output_dir": str(tmp_path / "week")},
                "last_day": {"conversation_count": 0, "output_dir": str(tmp_path / "day")},
            },
            "analysis": None,
        }

    monkeypatch.setattr(cli, "run_monitoring", fake_run_monitoring)

    assert cli.main(["--full-archive", "--skip-analysis"]) == 0
    assert captured["full_archive"] is True
    output = capsys.readouterr().out
    assert "Архив за 3 месяца" in output


def test_cli_standard_default_dry_run_shows_slug_directory(monkeypatch, capsys):
    from datetime import datetime, timezone
    from intercom_exporter.dates import DateTimeRange

    monkeypatch.setattr(cli, "default_range", lambda: DateTimeRange(
        datetime(2026, 10, 26, 8, 15, 30, tzinfo=timezone.utc),
        datetime(2026, 11, 2, 8, 15, 30, tzinfo=timezone.utc),
    ))

    assert cli.main(["--standard", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "20261026T081530Z_20261102T081530Z" in output


def test_cli_requires_standard_flag_for_explicit_dates():
    with pytest.raises(SystemExit) as error:
        cli.main(["--dry-run", "--start", "2026-07-01", "--end", "2026-10-01"])
    assert error.value.code == 2


def test_cli_standard_rejects_full_archive_flag():
    with pytest.raises(SystemExit) as error:
        cli.main(["--standard", "--dry-run", "--full-archive"])
    assert error.value.code == 2


def test_cli_monitoring_rejects_standard_only_selection_and_fields():
    with pytest.raises(SystemExit) as selection_error:
        cli.main(["--dry-run", "--selection", "updated"])
    assert selection_error.value.code == 2
    with pytest.raises(SystemExit) as fields_error:
        cli.main(["--dry-run", "--fields", "important"])
    assert fields_error.value.code == 2

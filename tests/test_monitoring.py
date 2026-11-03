import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from intercom_exporter.dates import DateRange, DateTimeRange
from intercom_exporter.monitoring import (
    build_monitoring_windows,
    create_monitoring_snapshots,
    record_has_activity_in_range,
    run_monitoring,
)


def test_monitoring_windows_roll_from_exact_launch_moment():
    now = datetime(2026, 11, 2, 8, 0, 0, tzinfo=timezone.utc)
    windows = build_monitoring_windows(now)
    assert windows["last_day"] == DateTimeRange(datetime(2026, 11, 1, 8, tzinfo=timezone.utc), now)
    assert windows["last_week"] == DateTimeRange(datetime(2026, 10, 26, 8, tzinfo=timezone.utc), now)
    assert windows["backup_3_months"] == DateTimeRange(datetime(2026, 8, 2, 8, tzinfo=timezone.utc), now)


@pytest.mark.parametrize(
    ("now", "expected_start"),
    [
        (datetime(2026, 5, 31, 12, tzinfo=timezone.utc), datetime(2026, 2, 28, 12, tzinfo=timezone.utc)),
        (datetime(2024, 5, 31, 12, tzinfo=timezone.utc), datetime(2024, 2, 29, 12, tzinfo=timezone.utc)),
        (datetime(2026, 3, 31, 12, tzinfo=timezone.utc), datetime(2025, 12, 31, 12, tzinfo=timezone.utc)),
    ],
)
def test_monitoring_archive_clamps_day_when_target_month_is_short(now, expected_start):
    assert build_monitoring_windows(now)["backup_3_months"].start == expected_start


def test_activity_window_includes_created_and_meaningful_part_timestamps():
    period = DateRange(date(2026, 10, 24), date(2026, 10, 31))

    assert record_has_activity_in_range({"created_at": "2026-10-24T00:00:00Z"}, period)
    assert record_has_activity_in_range({"created_at": "2026-10-01T00:00:00Z", "conversation_parts": {"conversation_parts": [{"part_type": "comment", "created_at": "2026-10-30T12:00:00Z"}]}}, period)
    assert not record_has_activity_in_range({"created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-30T12:00:00Z"}, period)
    assert not record_has_activity_in_range({"created_at": "2026-10-01T00:00:00Z", "conversation_parts": {"conversation_parts": [{"part_type": "bulk_reassignment", "created_at": "2026-10-30T12:00:00Z"}]}}, period)
    assert not record_has_activity_in_range({"created_at": "2026-10-23T23:59:59Z", "updated_at": "2026-10-31T00:00:00Z"}, period)


def test_create_monitoring_snapshots_preserves_archive_and_writes_window_files(tmp_path):
    export_dir = tmp_path / "2026-07-31_2026-10-31_run_20261031T120000Z"
    export_dir.mkdir()
    rows = [
        {"id": "old-week", "created_at": "2026-07-01T00:00:00Z", "updated_at": "2026-10-24T00:00:00Z", "private_field": "kept",
         "conversation_parts": {"conversation_parts": [{"part_type": "comment", "created_at": "2026-10-24T00:00:00Z"}]}},
        {"id": "old-day", "created_at": "2026-06-01T00:00:00Z", "updated_at": "2026-10-30T12:00:00Z",
         "conversation_parts": {"conversation_parts": [{"part_type": "comment", "created_at": "2026-10-30T12:00:00Z"}]}},
        {"id": "today", "created_at": "2026-10-31T00:00:00Z", "updated_at": "2026-10-31T00:00:00Z"},
        {"id": "old", "created_at": "2026-06-01T00:00:00Z", "updated_at": "2026-07-01T00:00:00Z"},
    ]
    source = export_dir / "conversations.json"
    source.write_text(json.dumps(rows), encoding="utf-8")
    (export_dir / "manifest.json").write_text(json.dumps({"start": "2026-07-31", "end_exclusive": "2026-10-31", "status": "success", "error_count": 0}), encoding="utf-8")
    result = create_monitoring_snapshots(
        {"output_dir": str(export_dir), "status": "success", "error_count": 0},
        build_monitoring_windows(datetime(2026, 10, 31, tzinfo=timezone.utc)),
    )

    backup = json.loads((export_dir / "backup_3_months" / "conversations.json").read_text(encoding="utf-8"))
    week = json.loads((export_dir / "last_week" / "conversations.json").read_text(encoding="utf-8"))
    day = json.loads((export_dir / "last_day" / "conversations.json").read_text(encoding="utf-8"))
    assert backup == rows
    assert backup[0]["private_field"] == "kept"
    assert [row["id"] for row in week] == ["old-week", "old-day"]
    assert [row["id"] for row in day] == ["old-day"]
    assert result["last_week"]["conversation_count"] == 2
    assert result["last_day"]["conversation_count"] == 1
    for name in ("backup_3_months", "last_week", "last_day"):
        manifest = json.loads((export_dir / name / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "success"
        assert "conversation_count" in manifest


def test_empty_window_skips_chatgpt_preparation_and_inherits_partial_status(tmp_path):
    export_dir = tmp_path / "run"
    export_dir.mkdir()
    (export_dir / "conversations.json").write_text(json.dumps([
        {"id": "today", "created_at": "2026-10-31T05:00:00Z", "updated_at": "2026-10-31T05:00:00Z"}
    ]), encoding="utf-8")
    (export_dir / "manifest.json").write_text(json.dumps({"limitations": ["one failed"]}), encoding="utf-8")
    result = create_monitoring_snapshots(
        {"output_dir": str(export_dir), "status": "partial", "error_count": 1},
        build_monitoring_windows(datetime(2026, 10, 31, tzinfo=timezone.utc)),
    )

    assert result["last_week"]["conversation_count"] == 0
    assert result["last_day"]["conversation_count"] == 0
    for name in ("backup_3_months", "last_week", "last_day"):
        manifest = json.loads((export_dir / name / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "partial"
        assert manifest["source_error_count"] == 1


def test_run_monitoring_uses_one_full_fields_export_with_api_version(tmp_path):
    from intercom_exporter.monitoring import run_monitoring

    windows = build_monitoring_windows(datetime(2026, 10, 31, tzinfo=timezone.utc))
    calls = []

    class FakeExporter:
        def run(self, period, output_root, **kwargs):
            calls.append((period, Path(output_root), kwargs))
            export_dir = Path(output_root) / "2026-07-31_2026-10-31_run_test"
            export_dir.mkdir(parents=True)
            (export_dir / "conversations.json").write_text("[]", encoding="utf-8")
            (export_dir / "manifest.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")
            return {"output_dir": str(export_dir), "status": "success", "error_count": 0}

    result = run_monitoring(
        FakeExporter(), tmp_path, full_archive=True, windows=windows, api_version="2.17", prepare=None
    )

    assert len(calls) == 1
    assert calls[0][0] == windows["backup_3_months"]
    assert calls[0][2] == {"api_version": "2.17", "selection": "all", "fields": "all", "timezone_name": "UTC"}
    assert result["snapshots"]["backup_3_months"]["conversation_count"] == 0


def test_run_monitoring_prepares_one_shared_analysis_from_both_windows(tmp_path):
    from intercom_exporter.monitoring import run_monitoring
    from intercom_exporter.support_analysis import prepare_monitoring_analysis

    windows = build_monitoring_windows(datetime(2026, 10, 31, tzinfo=timezone.utc))
    calls = []

    class FakeExporter:
        def run(self, period, output_root, **kwargs):
            export_dir = Path(output_root) / "run"
            export_dir.mkdir(parents=True)
            rows = [
                {"id": "week", "created_at": "2026-10-24T00:00:00Z"},
                {"id": "day", "created_at": "2026-10-30T00:00:00Z"},
            ]
            (export_dir / "conversations.json").write_text(json.dumps(rows), encoding="utf-8")
            (export_dir / "manifest.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")
            return {"output_dir": str(export_dir), "status": "success", "error_count": 0}

    def prepare(week_path, day_path, output_dir):
        calls.append((Path(week_path), Path(day_path), Path(output_dir)))
        return prepare_monitoring_analysis(week_path, day_path, output_dir)

    result = run_monitoring(FakeExporter(), tmp_path, full_archive=True, windows=windows, prepare=prepare)

    assert len(calls) == 1
    assert calls[0][0].name == "conversations.json"
    assert calls[0][0].parent.name == "last_week"
    assert calls[0][1].parent.name == "last_day"
    assert calls[0][2].name == "support-analysis"
    assert result["analysis"]["output_dir"] == str(calls[0][2])
    assert (calls[0][2] / "chatgpt_input_week.json").is_file()
    assert (calls[0][2] / "chatgpt_input_day.json").is_file()
    assert (calls[0][2] / "PROMPT.md").is_file()


def test_create_snapshots_without_archive_passes_week_through_and_filters_day(tmp_path):
    export_dir = tmp_path / "run"
    export_dir.mkdir()
    rows = [
        {"id": "week-only", "created_at": "2026-10-28T08:00:00Z", "updated_at": "2026-10-28T08:00:00Z"},
        {"id": "day", "created_at": "2026-10-25T00:00:00Z", "updated_at": "2026-10-31T11:00:00Z",
         "conversation_parts": {"conversation_parts": [{"part_type": "comment", "created_at": "2026-10-31T11:00:00Z"}]}},
        {"id": "stale", "created_at": "2026-10-20T00:00:00Z", "updated_at": "2026-10-20T00:00:00Z"},
    ]
    (export_dir / "conversations.json").write_text(json.dumps(rows), encoding="utf-8")
    (export_dir / "manifest.json").write_text(json.dumps({"status": "success", "error_count": 0}), encoding="utf-8")
    all_windows = build_monitoring_windows(datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc))
    windows = {"last_week": all_windows["last_week"], "last_day": all_windows["last_day"]}

    result = create_monitoring_snapshots(
        {"output_dir": str(export_dir), "status": "success", "error_count": 0},
        windows,
        source_window="last_week",
    )

    week = json.loads((export_dir / "last_week" / "conversations.json").read_text(encoding="utf-8"))
    day = json.loads((export_dir / "last_day" / "conversations.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in week] == ["week-only", "day", "stale"]
    assert [row["id"] for row in day] == ["day"]
    assert not (export_dir / "backup_3_months").exists()
    assert result["last_week"]["conversation_count"] == 3
    assert result["last_day"]["conversation_count"] == 1


def test_run_monitoring_without_archive_exports_only_week_window(tmp_path):
    windows = build_monitoring_windows(datetime(2026, 10, 31, 12, tzinfo=timezone.utc))
    calls = []

    class FakeExporter:
        def run(self, period, output_root, **kwargs):
            calls.append(period)
            export_dir = Path(output_root) / period.slug
            export_dir.mkdir(parents=True)
            (export_dir / "conversations.json").write_text("[]", encoding="utf-8")
            (export_dir / "manifest.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")
            return {"output_dir": str(export_dir), "status": "success", "error_count": 0}

    result = run_monitoring(FakeExporter(), tmp_path, windows=windows, prepare=None)

    assert calls == [windows["last_week"]]
    assert set(result["snapshots"]) == {"last_week", "last_day"}


def test_run_monitoring_with_full_archive_exports_backup_window(tmp_path):
    windows = build_monitoring_windows(datetime(2026, 10, 31, 12, tzinfo=timezone.utc))
    calls = []

    class FakeExporter:
        def run(self, period, output_root, **kwargs):
            calls.append(period)
            export_dir = Path(output_root) / period.slug
            export_dir.mkdir(parents=True)
            (export_dir / "conversations.json").write_text("[]", encoding="utf-8")
            (export_dir / "manifest.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")
            return {"output_dir": str(export_dir), "status": "success", "error_count": 0}

    result = run_monitoring(FakeExporter(), tmp_path, windows=windows, full_archive=True, prepare=None)

    assert calls == [windows["backup_3_months"]]
    assert set(result["snapshots"]) == {"backup_3_months", "last_week", "last_day"}


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 11, 2, 8, 0, 0),
        datetime(2026, 11, 2, 11, 0, 0, tzinfo=timezone(timedelta(hours=3))),
    ],
)
def test_monitoring_windows_normalize_input_to_utc(now):
    windows = build_monitoring_windows(now)
    assert windows["last_day"] == DateTimeRange(
        datetime(2026, 11, 1, 8, tzinfo=timezone.utc),
        datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
    )


def _export_two_conversations(output_root):
    export_dir = Path(output_root) / "run"
    export_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": "week", "created_at": "2026-10-24T00:00:00Z",
         "source": {"body": "текст"}, "conversation_parts": {"conversation_parts": []}},
        {"id": "day", "created_at": "2026-10-30T00:00:00Z",
         "source": {"body": "текст"}, "conversation_parts": {"conversation_parts": []}},
    ]
    (export_dir / "conversations.json").write_text(json.dumps(rows), encoding="utf-8")
    (export_dir / "manifest.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")
    return {"output_dir": str(export_dir), "status": "success", "error_count": 0}


def test_run_monitoring_creates_agents_bundle_by_default(tmp_path):
    from intercom_exporter.monitoring import run_monitoring
    from intercom_exporter.support_analysis import prepare_monitoring_analysis

    windows = build_monitoring_windows(datetime(2026, 10, 31, tzinfo=timezone.utc))

    class FakeExporter:
        def run(self, period, output_root, **kwargs):
            return _export_two_conversations(output_root)

    result = run_monitoring(FakeExporter(), tmp_path, windows=windows,
                            prepare=prepare_monitoring_analysis, agent_workers=3)

    bundle = result["analysis"]["agents_bundle"]
    assert bundle["taxonomy_source"] == "static_v5"
    assert bundle["workers"] == 2  # capped at the number of conversations
    assert (Path(result["analysis"]["output_dir"]) / "agents_work" / "manifest.json").is_file()


def test_run_monitoring_agents_bundle_disabled(tmp_path):
    from intercom_exporter.monitoring import run_monitoring
    from intercom_exporter.support_analysis import prepare_monitoring_analysis

    windows = build_monitoring_windows(datetime(2026, 10, 31, tzinfo=timezone.utc))

    class FakeExporter:
        def run(self, period, output_root, **kwargs):
            return _export_two_conversations(output_root)

    result = run_monitoring(FakeExporter(), tmp_path, windows=windows,
                            prepare=prepare_monitoring_analysis, agents=False)

    assert "agents_bundle" not in result["analysis"]
    assert not (Path(result["analysis"]["output_dir"]) / "agents_work").exists()

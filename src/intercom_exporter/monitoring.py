import json
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .dates import DateRange
from .support_analysis import prepare_monitoring_analysis


def _subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_index = divmod(month_index, 12)
    month = month_index + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def build_monitoring_windows(today: date | None = None) -> dict[str, DateRange]:
    current_day = today or datetime.now(timezone.utc).date()
    return {
        "backup_3_months": DateRange(_subtract_months(current_day, 3), current_day),
        "last_week": DateRange(current_day - timedelta(days=7), current_day),
        "last_day": DateRange(current_day - timedelta(days=1), current_day),
    }


def _timestamp_epoch(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp())


def _activity_timestamps(conversation: dict[str, Any]):
    yield conversation.get("created_at")
    yield conversation.get("updated_at")
    parts = conversation.get("conversation_parts") or []
    if isinstance(parts, dict):
        parts = parts.get("conversation_parts") or []
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict):
                yield part.get("created_at")


def record_has_activity_in_range(conversation: dict[str, Any], date_range: DateRange) -> bool:
    start, end = date_range.epoch_bounds()
    return any(
        (timestamp := _timestamp_epoch(value)) is not None and start <= timestamp < end
        for value in _activity_timestamps(conversation)
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def create_monitoring_snapshots(
    export_result: dict[str, Any],
    windows: dict[str, DateRange],
) -> dict[str, dict[str, Any]]:
    export_dir = Path(export_result["output_dir"])
    source_path = export_dir / "conversations.json"
    manifest_path = export_dir / "manifest.json"
    try:
        rows = json.loads(source_path.read_text(encoding="utf-8-sig"))
        export_manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Не удалось прочитать файлы полного экспорта для формирования срезов") from exc
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Полный экспорт должен быть JSON-массивом обращений")

    results: dict[str, dict[str, Any]] = {}
    for name, window in windows.items():
        selected = rows if name == "backup_3_months" else [
            row for row in rows if record_has_activity_in_range(row, window)
        ]
        snapshot_dir = export_dir / name
        snapshot_dir.mkdir()
        _write_json(snapshot_dir / "conversations.json", selected)
        status = export_result["status"]
        snapshot_manifest = {
            **export_manifest,
            "start": window.start.isoformat(),
            "end_exclusive": window.end.isoformat(),
            "selection": "all",
            "fields": "all",
            "timezone": export_result.get("timezone", "UTC"),
            "found_count": len(selected),
            "exported_count": len(selected),
            "conversation_count": len(selected),
            "status": status,
            "source_status": status,
            "error_count": export_result.get("error_count", 0),
            "source_error_count": export_result.get("error_count", 0),
            "limitations": export_manifest.get("limitations", []),
        }
        _write_json(snapshot_dir / "manifest.json", snapshot_manifest)
        results[name] = {
            "conversation_count": len(selected),
            "status": status,
            "output_dir": str(snapshot_dir),
        }

    source_path.unlink()
    manifest_path.unlink()
    return results


def run_monitoring(
    exporter,
    output_root: str | Path,
    *,
    windows: dict[str, DateRange] | None = None,
    api_version: str = "unknown",
    timezone_name: str = "UTC",
    prepare: Callable | None = prepare_monitoring_analysis,
) -> dict[str, Any]:
    window_set = windows or build_monitoring_windows()
    export_result = exporter.run(
        window_set["backup_3_months"], output_root,
        api_version=api_version, selection="all", fields="all", timezone_name=timezone_name,
    )
    snapshots = create_monitoring_snapshots(export_result, window_set)
    week_count = snapshots["last_week"]["conversation_count"]
    day_count = snapshots["last_day"]["conversation_count"]
    analysis = None
    if prepare is not None and (week_count or day_count):
        analysis = prepare(
            Path(snapshots["last_week"]["output_dir"]) / "conversations.json",
            Path(snapshots["last_day"]["output_dir"]) / "conversations.json",
            Path(export_result["output_dir"]) / "support-analysis",
        )
    return {"export": export_result, "snapshots": snapshots, "analysis": analysis}

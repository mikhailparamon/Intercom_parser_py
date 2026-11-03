import json
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .activity import has_meaningful_activity
from .dates import DateRange, DateTimeRange
from .support_analysis import prepare_monitoring_analysis


def _subtract_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_index = divmod(month_index, 12)
    month = month_index + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def build_monitoring_windows(now: datetime | None = None) -> dict[str, DateTimeRange]:
    """Скользящие окна от момента запуска (UTC): 3 месяца, 7×24 часа, 24 часа."""
    current = now or datetime.now(timezone.utc)
    current = current.astimezone(timezone.utc) if current.tzinfo else current.replace(tzinfo=timezone.utc)
    return {
        "backup_3_months": DateTimeRange(_subtract_months(current, 3), current),
        "last_week": DateTimeRange(current - timedelta(days=7), current),
        "last_day": DateTimeRange(current - timedelta(days=1), current),
    }


def record_has_activity_in_range(conversation: dict[str, Any], date_range: DateRange | DateTimeRange) -> bool:
    start, end = date_range.epoch_bounds()
    return has_meaningful_activity(conversation, start, end)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def create_monitoring_snapshots(
    export_result: dict[str, Any],
    windows: dict[str, DateTimeRange],
    *,
    source_window: str = "backup_3_months",
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
        selected = rows if name == source_window else [
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
    full_archive: bool = False,
    windows: dict[str, DateTimeRange] | None = None,
    api_version: str = "unknown",
    timezone_name: str = "UTC",
    prepare: Callable | None = prepare_monitoring_analysis,
    agents: bool = True,
    agent_workers: int = 10,
) -> dict[str, Any]:
    window_set = windows or build_monitoring_windows()
    source_name = "backup_3_months" if full_archive else "last_week"
    export_result = exporter.run(
        window_set[source_name], output_root,
        api_version=api_version, selection="all", fields="all", timezone_name=timezone_name,
    )
    snapshot_names = ("backup_3_months", "last_week", "last_day") if full_archive else ("last_week", "last_day")
    snapshot_windows = {name: window_set[name] for name in snapshot_names}
    snapshots = create_monitoring_snapshots(export_result, snapshot_windows, source_window=source_name)
    week_count = snapshots["last_week"]["conversation_count"]
    day_count = snapshots["last_day"]["conversation_count"]
    analysis = None
    if prepare is not None and (week_count or day_count):
        analysis = prepare(
            Path(snapshots["last_week"]["output_dir"]) / "conversations.json",
            Path(snapshots["last_day"]["output_dir"]) / "conversations.json",
            Path(export_result["output_dir"]) / "support-analysis",
        )
        if agents and analysis:
            from .agents_orchestration import prepare_agents_bundle

            bundle = prepare_agents_bundle(
                Path(snapshots["last_week"]["output_dir"]) / "conversations.json",
                Path(snapshots["last_day"]["output_dir"]) / "conversations.json",
                Path(analysis["output_dir"]),
                workers=agent_workers,
            )
            analysis = {**analysis, "agents_bundle": bundle}
    return {"export": export_result, "snapshots": snapshots, "analysis": analysis}

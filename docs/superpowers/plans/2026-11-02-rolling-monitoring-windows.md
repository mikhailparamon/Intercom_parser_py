# Скользящие окна мониторинга и опциональный архив — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Мониторинг по умолчанию выгружает только скользящую неделю (ровно 7×24 ч) и делает из неё срез за скользящие сутки (ровно 24 ч); архив за 3 месяца включается флагом `--full-archive`. Режим `--standard` по умолчанию выгружает скользящую неделю вместо 3 месяцев.

**Architecture:** Новый тип `DateTimeRange` (точность до секунды, UTC) рядом с существующим `DateRange` (календарные даты, остаётся для явных `--start/--end`). Оба дают `epoch_bounds()`, `contains()`, `slug` (имя каталога, безопасное для Windows). `search_conversations` принимает и `date`, и `datetime`. `run_monitoring` получает `full_archive: bool`; без него источником срезов служит недельный экспорт.

**Tech Stack:** Python 3.11+, pytest, argparse, Intercom Search API.

**Prerequisite:** Рабочее дерево содержит незакоммиченные изменения (ветка `мониторинг`). Перед Task 1 закоммитить их отдельным коммитом, сообщение согласовать с пользователем. Все коммиты плана — в текущую ветку `мониторинг`.

**Решения по требованиям (подтверждены пользователем):**
1. Границы — скользящие от момента запуска: день = ровно последние 24 часа, неделя = ровно 7×24 часа, UTC.
2. Архив за 3 месяца — CLI-флаг `--full-archive`.
3. Без архива: один запрос к API за неделю + локальный срез дня.
4. `--standard`: период по умолчанию сокращается с 3 месяцев до скользящей недели.

---

### Task 1: `DateTimeRange` в `dates.py`

**Files:**
- Modify: `src/intercom_exporter/dates.py`
- Test: `tests/test_dates.py`

- [ ] **Step 1: Write the failing tests**

Добавить в `tests/test_dates.py` (импорты сверху файла обновить: `from intercom_exporter.dates import DateRange, DateTimeRange, default_range, parse_date_range`):

```python
def test_datetime_range_epoch_bounds_use_exact_utc_seconds():
    window = DateTimeRange(
        datetime(2026, 11, 1, 8, 30, 15, tzinfo=timezone.utc),
        datetime(2026, 11, 2, 8, 30, 15, tzinfo=timezone.utc),
    )
    start, end = window.epoch_bounds()
    assert end - start == 24 * 3600
    assert window.contains(start)
    assert window.contains(end - 1)
    assert not window.contains(end)


def test_datetime_range_normalizes_naive_and_offsets_to_utc():
    window = DateTimeRange(
        datetime(2026, 11, 1, 8),
        datetime(2026, 11, 2, 11, tzinfo=timezone(timedelta(hours=3))),
    )
    assert window.start == datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
    assert window.end == datetime(2026, 11, 2, 8, tzinfo=timezone.utc)


def test_datetime_range_rejects_inverted_bounds():
    with pytest.raises(ValueError, match="позже"):
        DateTimeRange(
            datetime(2026, 11, 2, tzinfo=timezone.utc),
            datetime(2026, 11, 1, tzinfo=timezone.utc),
        )


def test_datetime_range_slug_is_filesystem_safe():
    window = DateTimeRange(
        datetime(2026, 11, 1, 8, tzinfo=timezone.utc),
        datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
    )
    assert window.slug == "20261101T080000Z_20261102T080000Z"


def test_date_range_slug_matches_current_dir_naming():
    assert DateRange(date(2026, 7, 1), date(2026, 10, 1)).slug == "2026-07-01_2026-10-01"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dates.py -v`
Expected: FAIL, `ImportError: cannot import name 'DateTimeRange'`

- [ ] **Step 3: Implement `DateTimeRange`**

В `src/intercom_exporter/dates.py` добавить свойство `slug` в `DateRange` (после метода `contains`):

```python
    @property
    def slug(self) -> str:
        return f"{self.start.isoformat()}_{self.end.isoformat()}"
```

Добавить после класса `DateRange`:

```python
def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class DateTimeRange:
    """Скользящий период с точностью до секунды; границы хранятся в UTC."""

    start: datetime
    end: datetime

    def __post_init__(self):
        start = _ensure_utc(self.start)
        end = _ensure_utc(self.end)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        if end <= start:
            raise ValueError("Момент окончания должен быть позже момента начала")

    def epoch_bounds(self) -> tuple[int, int]:
        return int(self.start.timestamp()), int(self.end.timestamp())

    def contains(self, epoch_seconds: int) -> bool:
        start, end = self.epoch_bounds()
        return start <= int(epoch_seconds) < end

    @property
    def slug(self) -> str:
        stamp = "%Y%m%dT%H%M%SZ"
        return f"{self.start.strftime(stamp)}_{self.end.strftime(stamp)}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dates.py -v`
Expected: PASS (новые 5 тестов)

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/dates.py tests/test_dates.py
git commit -m "feat(dates): add DateTimeRange with second precision and filesystem-safe slug"
```

---

### Task 2: `default_range` → скользящая неделя

**Files:**
- Modify: `src/intercom_exporter/dates.py`
- Test: `tests/test_dates.py`

- [ ] **Step 1: Replace failing tests**

В `tests/test_dates.py` удалить `test_default_range_is_last_three_calendar_months_through_today` и `test_default_range_clamps_day_when_target_month_is_short`, добавить:

```python
def test_default_range_is_rolling_seven_days():
    now = datetime(2026, 11, 2, 8, 15, 30, tzinfo=timezone.utc)
    result = default_range(now)
    assert result == DateTimeRange(datetime(2026, 10, 26, 8, 15, 30, tzinfo=timezone.utc), now)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dates.py::test_default_range_is_rolling_seven_days -v`
Expected: FAIL (default_range возвращает DateRange за 3 месяца)

- [ ] **Step 3: Implement**

В `src/intercom_exporter/dates.py` заменить тело `default_range`:

```python
def default_range(now: datetime | None = None) -> DateTimeRange:
    """Скользящая неделя: ровно последние 7 * 24 часа от момента запуска (UTC)."""
    current = now or datetime.now(timezone.utc)
    return DateTimeRange(current - timedelta(days=7), current)
```

Удалить ставший ненужным импорт `from calendar import monthrange` (проверить, что в файле больше не используется).

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dates.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/dates.py tests/test_dates.py
git commit -m "feat(dates): default_range returns rolling 7x24h window"
```

---

### Task 3: `search_conversations` принимает `datetime`

**Files:**
- Modify: `src/intercom_exporter/client.py:90-94`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_client.py` (импорт `datetime` уже есть в файле — проверить, при необходимости добавить `from datetime import datetime, timezone`):

```python
def test_search_accepts_exact_datetimes_with_second_precision():
    session = FakeSession([FakeResponse(payload={"conversations": [], "pages": {}})])
    client = IntercomClient("t", "2.17", session=session, sleeper=lambda _: None)
    start = datetime(2026, 11, 1, 8, 30, 15, tzinfo=timezone.utc)
    end = datetime(2026, 11, 2, 8, 30, 15, tzinfo=timezone.utc)

    list(client.search_conversations(start, end))

    creation_filters = session.calls[0][2]["json"]["query"]["value"][0]["value"]
    assert creation_filters[0] == {"field": "created_at", "operator": ">", "value": str(int(start.timestamp()) - 1)}
    assert creation_filters[1] == {"field": "created_at", "operator": "<", "value": str(int(end.timestamp()))}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_client.py::test_search_accepts_exact_datetimes_with_second_precision -v`
Expected: FAIL (datetime трактуется как дата полуночи либо падает)

- [ ] **Step 3: Implement**

В `src/intercom_exporter/client.py` заменить начало `search_conversations` (строки `start_dt = ...` … `end_epoch = int(end_dt.timestamp())`):

```python
    @staticmethod
    def _epoch(value: date | datetime) -> int:
        if isinstance(value, datetime):
            moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return int(moment.astimezone(timezone.utc).timestamp())
        return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp())

    def search_conversations(self, start: date | datetime, end: date | datetime) -> Iterator[dict[str, Any]]:
        start_epoch = self._epoch(start)
        end_epoch = self._epoch(end)
```

(старые строки `start_dt = ...`, `end_dt = ...`, `start_epoch = ...`, `end_epoch = ...` удалить; остальное тело метода без изменений)

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_client.py -v`
Expected: PASS, включая старые тесты с `date`

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/client.py tests/test_client.py
git commit -m "feat(client): search_conversations accepts exact datetimes"
```

---

### Task 4: Экспортёр принимает `DateTimeRange`

**Files:**
- Modify: `src/intercom_exporter/exporter.py` (импорт, `_create_output_dir`, сигнатура `run`)
- Test: `tests/test_exporter_storage.py`

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_exporter_storage.py` (импорты: `from intercom_exporter.dates import DateRange, DateTimeRange`):

```python
def test_run_with_datetime_range_names_dir_by_slug_and_writes_exact_manifest(tmp_path):
    period = DateTimeRange(
        datetime(2026, 11, 1, 8, tzinfo=timezone.utc),
        datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
    )
    start, _ = period.epoch_bounds()
    client = FakeClient([candidate("c1", start)], {"c1": detail("c1", [])})

    result = ConversationExporter(client).run(period, tmp_path)

    assert Path(result["output_dir"]).name == "20261101T080000Z_20261102T080000Z"
    manifest = json.loads((Path(result["output_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["start"] == "2026-11-01T08:00:00+00:00"
    assert manifest["end_exclusive"] == "2026-11-02T08:00:00+00:00"
```

(`FakeClient`, `candidate`, `detail` уже определены в начале файла.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exporter_storage.py::test_run_with_datetime_range_names_dir_by_slug_and_writes_exact_manifest -v`
Expected: FAIL (имя каталога содержит `:` из `datetime.isoformat()` — недопустимо/не совпадает со slug)

- [ ] **Step 3: Implement**

В `src/intercom_exporter/exporter.py`:

1. Импорт: `from .dates import DateRange, DateTimeRange`
2. В `_create_output_dir` сигнатуру изменить на `date_range: DateRange | DateTimeRange`, а строку `base_name = f"{date_range.start.isoformat()}_{date_range.end.isoformat()}"` заменить на:

```python
        base_name = date_range.slug
```

3. В `run` сигнатуру изменить на `date_range: DateRange | DateTimeRange`. Строки манифеста `date_range.start.isoformat()` / `date_range.end.isoformat()` не трогать — работают для обоих типов.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exporter_storage.py -v`
Expected: PASS (старые тесты с `DateRange` не затронуты — slug `DateRange` совпадает с прежним именем каталога)

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/exporter.py tests/test_exporter_storage.py
git commit -m "feat(exporter): accept DateTimeRange, name output dirs via range slug"
```

---

### Task 5: `build_monitoring_windows` → скользящие окна с точным временем

**Files:**
- Modify: `src/intercom_exporter/monitoring.py` (импорты, `_subtract_months`, `build_monitoring_windows`)
- Test: `tests/test_monitoring.py`

- [ ] **Step 1: Replace failing tests**

В `tests/test_monitoring.py` обновить импорты: `from datetime import date, datetime, timezone` и `from intercom_exporter.dates import DateRange, DateTimeRange`. Заменить `test_monitoring_windows_use_three_months_and_completed_days` на:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_monitoring.py -k "roll_from_exact or clamps_day" -v`
Expected: FAIL (сейчас окна календарные, end без времени)

- [ ] **Step 3: Implement**

В `src/intercom_exporter/monitoring.py`:

1. Импорты: убрать `date` из `from datetime import ...` (станет не нужен), добавить `DateTimeRange` в `from .dates import ...`.
2. Заменить `_subtract_months` и `build_monitoring_windows`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_monitoring.py -k "roll_from_exact or clamps_day" -v`
Expected: PASS. Остальные тесты файла пока падают — чинятся в Task 6.

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/monitoring.py tests/test_monitoring.py
git commit -m "feat(monitoring): rolling windows with exact launch time"
```

---

### Task 6: `create_monitoring_snapshots(source_window=...)` и `run_monitoring(full_archive=...)`

**Files:**
- Modify: `src/intercom_exporter/monitoring.py` (`create_monitoring_snapshots`, `run_monitoring`)
- Test: `tests/test_monitoring.py`

- [ ] **Step 1: Write the failing tests**

Добавить в `tests/test_monitoring.py`:

```python
def test_create_snapshots_without_archive_filters_week_and_day_from_week_export(tmp_path):
    export_dir = tmp_path / "run"
    export_dir.mkdir()
    rows = [
        {"id": "week-only", "created_at": "2026-10-28T08:00:00Z", "updated_at": "2026-10-28T08:00:00Z"},
        {"id": "day", "created_at": "2026-10-25T00:00:00Z", "updated_at": "2026-10-31T11:00:00Z"},
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
    assert [row["id"] for row in week] == ["week-only", "day"]
    assert [row["id"] for row in day] == ["day"]
    assert not (export_dir / "backup_3_months").exists()
    assert result["last_week"]["conversation_count"] == 2
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
```

Обновить существующие тесты этого файла:
- `test_create_monitoring_snapshots_preserves_archive_and_writes_window_files`: `build_monitoring_windows(date(2026, 10, 31))` → `build_monitoring_windows(datetime(2026, 10, 31, tzinfo=timezone.utc))`. Ожидания (`week == [old-week, old-day]`, `day == [old-day]`) не меняются: окна `[10-24T00:00, 10-31T00:00)` и `[10-30T00:00, 10-31T00:00)` совпадают с прежними границами.
- `test_empty_window_skips_chatgpt_preparation_and_inherits_partial_status`: та же замена `date(...)` → `datetime(2026, 10, 31, tzinfo=timezone.utc)`.
- `test_run_monitoring_uses_one_full_fields_export_with_api_version`: `build_monitoring_windows(date(2026, 10, 31))` → `datetime(2026, 10, 31, tzinfo=timezone.utc)`; в вызов `run_monitoring(...)` добавить `full_archive=True` (утверждение `calls[0][0] == windows["backup_3_months"]` сохраняется).
- `test_run_monitoring_prepares_one_shared_analysis_from_both_windows`: `build_monitoring_windows(date(2026, 10, 31))` → `datetime(2026, 10, 31, tzinfo=timezone.utc)`; добавить `full_archive=True` в вызов (срезы недели/дня строятся из архива, как раньше).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_monitoring.py -v`
Expected: FAIL у новых тестов (`TypeError: ... source_window` / `full_archive`) и у старых вызовов с `date(...)`

- [ ] **Step 3: Implement**

В `src/intercom_exporter/monitoring.py`:

1. Сигнатура и фильтрация в `create_monitoring_snapshots`:

```python
def create_monitoring_snapshots(
    export_result: dict[str, Any],
    windows: dict[str, DateTimeRange],
    *,
    source_window: str = "backup_3_months",
) -> dict[str, dict[str, Any]]:
```

и строку `selected = rows if name == "backup_3_months" else [...]` заменить на:

```python
        selected = rows if name == source_window else [
            row for row in rows if record_has_activity_in_range(row, window)
        ]
```

2. В `run_monitoring` добавить параметр и выбор окна-источника:

```python
def run_monitoring(
    exporter,
    output_root: str | Path,
    *,
    full_archive: bool = False,
    windows: dict[str, DateTimeRange] | None = None,
    api_version: str = "unknown",
    timezone_name: str = "UTC",
    prepare: Callable | None = prepare_monitoring_analysis,
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
```

(остальное тело `run_monitoring` без изменений)

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_monitoring.py -v`
Expected: PASS (весь файл)

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/monitoring.py tests/test_monitoring.py
git commit -m "feat(monitoring): optional full archive via full_archive flag, week export by default"
```

---

### Task 7: CLI — флаг `--full-archive`, dry-run, вывод

**Files:**
- Modify: `src/intercom_exporter/cli.py`
- Test: `tests/test_config_cli.py`

- [ ] **Step 1: Write the failing tests**

В `tests/test_config_cli.py` заменить `test_cli_defaults_to_monitoring_and_dry_run_shows_rolling_windows` на:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_cli.py -v`
Expected: FAIL (`unrecognized arguments: --full-archive`, `архив выключен` отсутствует)

- [ ] **Step 3: Implement**

В `src/intercom_exporter/cli.py`:

1. В `build_parser()` после `--standard` добавить:

```python
    parser.add_argument("--full-archive", action="store_true",
                        help="Мониторинг: дополнительно выгрузить архив за последние 3 месяца")
```

2. В ветке мониторинга блок dry-run заменить на:

```python
        windows = build_monitoring_windows()
        if args.dry_run:
            source = windows["backup_3_months"] if args.full_archive else windows["last_week"]
            base_output_dir = Path(args.output) / source.slug
            if args.full_archive:
                archive_line = f"архив [{windows['backup_3_months'].start.isoformat()}, {windows['backup_3_months'].end.isoformat()}); "
            else:
                archive_line = "архив выключен (включить: --full-archive); "
            print(
                "Мониторинг UTC: " + archive_line +
                f"неделя [{windows['last_week'].start.isoformat()}, {windows['last_week'].end.isoformat()}); "
                f"день [{windows['last_day'].start.isoformat()}, {windows['last_day'].end.isoformat()}); "
                f"поля: all; базовый каталог: {base_output_dir} (при повторном запуске будет создан новый каталог)"
            )
            return 0
```

3. Вызов `run_monitoring` — добавить `full_archive=args.full_archive`:

```python
            result = run_monitoring(
                exporter, args.output, full_archive=args.full_archive, windows=windows, api_version=config.api_version,
                timezone_name=args.timezone,
                prepare=None if args.skip_analysis else prepare_monitoring_analysis,
            )
```

4. Строку `f"Экспорт архива завершён: ...` переименовать в `f"Экспорт завершён: ...` (остальное в f-строке без изменений).

5. Цикл печати срезов заменить на:

```python
        labels = [("last_week", "Срез за неделю"), ("last_day", "Срез за день")]
        if args.full_archive:
            labels.insert(0, ("backup_3_months", "Архив за 3 месяца"))
        for name, label in labels:
            snapshot = result["snapshots"][name]
            print(f"{label}: {snapshot['conversation_count']} обращений. Каталог: {snapshot['output_dir']}")
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_cli.py -v`
Expected: PASS (весь файл, включая старые тесты — `--standard` не изменялся, а monkeypatch `run_monitoring` в `test_monitoring_cli_runs_one_openrouter_analysis_for_week_and_day` принимает `**kwargs`)

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/cli.py tests/test_config_cli.py
git commit -m "feat(cli): --full-archive flag; monitoring without archive by default"
```

---

### Task 8: Обновить README.md

**Files:**
- Modify: `README.md` (строки ~106, ~136-148, ~179)

- [ ] **Step 1: Update monitoring description (line ~106)**

Заменить абзац «Запуск без параметров включает режим мониторинга…» на:

```markdown
Запуск без параметров включает режим мониторинга. Он выгружает полный набор полей Intercom для обращений с активностью за скользящую неделю — ровно последние 7×24 часа от момента запуска, — и выделяет из неё срез за скользящие сутки (ровно последние 24 часа). Например, при запуске 2 ноября 2026 года в 08:00:00 UTC неделя охватывает `[2026-10-26T08:00:00Z, 2026-11-02T08:00:00Z)`, а день — `[2026-11-01T08:00:00Z, 2026-11-02T08:00:00Z)`. С флагом `--full-archive` вместо недели выгружается архив за последние три месяца, и оба среза выделяются из него. Начало трёхмесячного периода ограничивается последним днём целевого месяца, если исходный день там отсутствует.
```

- [ ] **Step 2: Add flag row to options table (after the `--standard` row, line ~136)**

```markdown
| `--full-archive` | Мониторинг: дополнительно выгрузить архив за последние три месяца. Без флага выгружается только скользящая неделя. |
```

- [ ] **Step 3: Update mechanics paragraph (line ~148)**

Заменить абзац «Мониторинг выполняет один запрос…» на:

```markdown
Мониторинг без `--full-archive` выполняет один запрос полного набора за скользящую неделю с `selection=all` и `fields=all`, затем формирует из него локальный срез дня по `created_at`, `updated_at` или времени любой части переписки. С `--full-archive` запрос выполняется за три месяца, и оба среза выделяются из архива. Результаты находятся в уникальной папке запуска: `last_week/` и `last_day/` (плюс `backup_3_months/` при `--full-archive`); в каждой лежат `conversations.json` и `manifest.json`.
```

- [ ] **Step 4: Update manifest paragraph (line ~179)**

Фразу «В мониторинге эти файлы лежат в каждой из трёх папок-срезов» заменить на «В мониторинге эти файлы лежат в каждой папке-срезе (две без `--full-archive`, три с ним)».

- [ ] **Step 5: Update `--standard` default description**

В строке таблицы про `--standard` (или в абзаце про обычный экспорт) указать: без `--start/--end` обычный экспорт выгружает скользящую неделю — ровно последние 7×24 часа от момента запуска. Если в README нет явного упоминания прежнего умолчания — добавить одно предложение в раздел обычного экспорта.

- [ ] **Step 6: Verify + commit**

Run: `.venv/Scripts/python.exe -m pytest -v` и `.venv/Scripts/python.exe main.py --dry-run` (проверить, что вывод совпадает с новым описанием)
Expected: все тесты PASS; dry-run печатает «архив выключен» и скользящие окна с временем

```bash
git add README.md
git commit -m "docs: describe rolling windows and --full-archive"
```

---

## Self-Review пройден

- **Spec coverage:** (1) по умолчанию без 3-месячного сбора → Task 6, 7; (2) флаг включения архива → Task 6, 7; (3) точные скользящие границы 24 ч / 7×24 ч → Task 1, 5; (4) `--standard` сокращён до недели → Task 2.
- **Placeholder scan:** код приведён во всех шагах, изменяющих код.
- **Type consistency:** `DateTimeRange`, `slug`, `epoch_bounds`, `contains`, `source_window`, `full_archive` используются одинаково во всех задачах. `record_has_activity_in_range` принимает любой диапазон с `epoch_bounds()` — сигнатуру менять не нужно.

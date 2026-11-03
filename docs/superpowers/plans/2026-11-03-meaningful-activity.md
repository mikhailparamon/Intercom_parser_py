# Осмысленная активность вместо «любой активности» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Системные события Intercom (`bulk_reassignment`, `assignment`, `conversation_attribute_updated_by_admin`, `snoozed`, `timer_unsnooze` и т.п.) и само поле `updated_at` больше не считаются «активностью» при отборе обращений. Активностью считаются только содержательные части переписки: `comment`, `open`, `note`, `close` — плюс само создание обращения (`created_at`).

**Architecture:** Единый модуль `activity.py` с константой `MEANINGFUL_PART_TYPES` и функцией проверки; `monitoring.py` (срезы last_week/last_day) и `exporter.py` (отбор при экспорте, selection=new/updated/all) используют его вместо собственных проверок `updated_at` + любых частей. Один источник правды, синхронизированный с README.

**Tech Stack:** Python 3, pytest.

**Файлы:**
- Create: `src/intercom_exporter/activity.py`
- Modify: `src/intercom_exporter/monitoring.py` (`_activity_timestamps`, `record_has_activity_in_range`)
- Modify: `src/intercom_exporter/exporter.py` (блок selection в `run`, строки ~133–149)
- Create: `tests/test_activity.py`
- Modify: `tests/` — тесты, завязанные на старое поведение `updated_at` (найти по `record_has_activity_in_range`, `updated_at` в test_monitoring.py, test_exporter.py)
- Modify: `README.md` (правило отбора, разделы «Использование» и «Правило отбора и история»)

**Запуск тестов:** `.venv/Scripts/python.exe -m pytest tests/ -q` (Windows, из корня проекта). Известный предсуществующий сбой `tests/test_support_analysis.py::test_prepare_analyze_dahl_uses_dahl_settings` — игнорировать.

---

### Task 1: Модуль `activity.py` — единое правило осмысленной активности

**Files:**
- Create: `src/intercom_exporter/activity.py`
- Test: `tests/test_activity.py`

- [ ] **Step 1: Write the failing test**

Создать `tests/test_activity.py`:

```python
from datetime import datetime, timezone

from intercom_exporter.activity import MEANINGFUL_PART_TYPES, has_meaningful_activity


def _ts(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


START = _ts("2026-11-01T00:00:00Z")
END = _ts("2026-11-03T00:00:00Z")


def _conv(created_at, parts=(), updated_at=None):
    return {
        "created_at": created_at,
        "updated_at": updated_at,
        "conversation_parts": {"conversation_parts": list(parts)},
    }


def _part(part_type, created_at):
    return {"part_type": part_type, "created_at": created_at}


def test_created_inside_window_counts():
    assert has_meaningful_activity(_conv(_ts("2026-11-02T10:00:00Z")), START, END) is True


def test_created_outside_without_parts_does_not_count():
    assert has_meaningful_activity(_conv(_ts("2026-07-08T10:00:00Z")), START, END) is False


def test_updated_at_alone_does_not_count():
    conv = _conv(_ts("2026-07-08T10:00:00Z"), updated_at=_ts("2026-11-02T15:00:00Z"))
    assert has_meaningful_activity(conv, START, END) is False


def test_bulk_reassignment_does_not_count():
    conv = _conv(_ts("2026-07-08T10:00:00Z"),
                 [_part("bulk_reassignment", _ts("2026-11-02T15:00:00Z"))],
                 updated_at=_ts("2026-11-02T15:00:00Z"))
    assert has_meaningful_activity(conv, START, END) is False


def test_user_comment_counts():
    conv = _conv(_ts("2026-07-08T10:00:00Z"), [_part("comment", _ts("2026-11-02T19:14:54Z"))])
    assert has_meaningful_activity(conv, START, END) is True


def test_meaningful_part_types_set():
    assert MEANINGFUL_PART_TYPES == {"comment", "open", "note", "close"}


def test_each_meaningful_type_counts_and_system_types_do_not():
    for good in ("comment", "open", "note", "close"):
        conv = _conv(_ts("2026-07-08T10:00:00Z"), [_part(good, _ts("2026-11-02T12:00:00Z"))])
        assert has_meaningful_activity(conv, START, END) is True, good
    for bad in ("bulk_reassignment", "assignment", "conversation_attribute_updated_by_admin",
                "snoozed", "timer_unsnooze", ""):
        conv = _conv(_ts("2026-07-08T10:00:00Z"), [_part(bad, _ts("2026-11-02T12:00:00Z"))])
        assert has_meaningful_activity(conv, START, END) is False, bad


def test_part_outside_window_does_not_count():
    conv = _conv(_ts("2026-07-08T10:00:00Z"), [_part("comment", _ts("2026-10-31T23:59:59Z"))])
    assert has_meaningful_activity(conv, START, END) is False


def test_unix_timestamps_supported():
    conv = {"created_at": _ts("2026-11-02T10:00:00Z"), "conversation_parts": []}
    assert has_meaningful_activity(conv, START, END) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_activity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'intercom_exporter.activity'`

- [ ] **Step 3: Write the implementation**

Создать `src/intercom_exporter/activity.py`:

```python
"""Единое правило осмысленной активности обращения.

Активностью считаются только содержательные части переписки (comment, open,
note, close) и само создание обращения. Поле updated_at и системные события
(bulk_reassignment, assignment, conversation_attribute_updated_by_admin,
snoozed, timer_unsnooze и прочие) активностью не считаются: массовые
админские операции обновляют updated_at тысяч обращений разом, хотя клиент
ничего не писал. Правило используют и exporter (отбор при выгрузке), и
monitoring (срезы неделя/день). Менять только синхронно с README.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

MEANINGFUL_PART_TYPES = frozenset({"comment", "open", "note", "close"})


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


def _in_range(value: Any, start: int, end: int) -> bool:
    timestamp = _timestamp_epoch(value)
    return timestamp is not None and start <= timestamp < end


def has_meaningful_activity(conversation: dict[str, Any], start: int, end: int) -> bool:
    """True, если обращение создано в окне или имеет содержательную часть в окне."""
    if _in_range(conversation.get("created_at"), start, end):
        return True
    parts = conversation.get("conversation_parts") or []
    if isinstance(parts, dict):
        parts = parts.get("conversation_parts") or []
    if not isinstance(parts, list):
        return False
    return any(
        isinstance(part, dict)
        and part.get("part_type") in MEANINGFUL_PART_TYPES
        and _in_range(part.get("created_at"), start, end)
        for part in parts
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_activity.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/activity.py tests/test_activity.py
git commit -m "feat: shared meaningful-activity rule (comment/open/note/close + created_at)"
```

---

### Task 2: Применить правило в `monitoring.py` и `exporter.py`

**Files:**
- Modify: `src/intercom_exporter/monitoring.py`
- Modify: `src/intercom_exporter/exporter.py`
- Modify: `tests/test_monitoring.py`, `tests/test_exporter.py` (тесты, завязанные на `updated_at`-активность — найти и обновить под новое правило)

- [ ] **Step 1: Найти тесты, завязанные на старое правило**

Run: `.venv/Scripts/python.exe -m pytest tests/test_monitoring.py tests/test_exporter.py -q` (базовая линия) и `grep -n "updated_at\|record_has_activity" tests/test_monitoring.py tests/test_exporter.py` — выпиши тесты, которые проверяют отбор по `updated_at` или по частям любых типов. Они должны быть обновлены в Step 4.

- [ ] **Step 2: monitoring.py**

В `src/intercom_exporter/monitoring.py`:

- Удалить функции `_timestamp_epoch` и `_activity_timestamps` (переезжают в `activity.py`).
- Заменить `record_has_activity_in_range` на делегирование:

```python
from .activity import has_meaningful_activity


def record_has_activity_in_range(conversation: dict[str, Any], date_range: DateRange | DateTimeRange) -> bool:
    start, end = date_range.epoch_bounds()
    return has_meaningful_activity(conversation, start, end)
```

- Убрать теперь неиспользуемые импорты (если `datetime`/`timezone` больше не нужны вне `_subtract_months`/`build_monitoring_windows` — проверить).

- [ ] **Step 3: exporter.py**

В `src/intercom_exporter/exporter.py` в блоке отбора (строки ~133–149) заменить логику на осмысленную активность:

```python
                                created_at = candidate.get("created_at", detail.get("created_at"))
                                conv_for_check = {
                                    "created_at": created_at if created_at is not None else detail.get("created_at"),
                                    "conversation_parts": parts,
                                }
                                from .activity import MEANINGFUL_PART_TYPES, has_meaningful_activity  # наверх файла
                                start_epoch, end_epoch = date_range.epoch_bounds()
                                created_matches = in_period(created_at) or in_period(detail.get("created_at"))
                                meaningful = has_meaningful_activity(conv_for_check, start_epoch, end_epoch)
                                updated_matches = in_period(candidate.get("updated_at")) or in_period(detail.get("updated_at"))
                                if selection == "new":
                                    selected = created_matches
                                elif selection == "updated":
                                    try:
                                        is_old = created_at is not None and int(created_at) < start_epoch
                                    except (TypeError, ValueError, OverflowError):
                                        is_old = False
                                    selected = is_old and meaningful
                                else:
                                    selected = created_matches or meaningful
```

Точнее: импорт `from .activity import MEANINGFUL_PART_TYPES, has_meaningful_activity` вынести в шапку файла; `part_matches` и прямое сравнение по всем частям убрать. `updated_matches` остаётся вычисляться только если используется где-то ещё (например в ветке `truncated`); если нет — удалить. Обрати внимание: `conv_for_check` должен содержать части в исходном (Unix) виде — на этом этапе `parts` ещё не нормализованы, `_timestamp_epoch` в activity.py поддерживает и int, и ISO-строки.

Ветка `truncated`-предупреждений (строки ~150–155) ссылается на `created_matches` и selection — сохранить семантику; поправить только если условие использует удалённые переменные.

- [ ] **Step 4: Обновить тесты под новое правило**

По списку из Step 1: тесты, ожидавшие включения обращения по голому `updated_at` или по системной части (`assignment` и т.п.), переписать так, чтобы активность задавалась содержательной частью (`comment`/`open`/`note`/`close`) в периоде. Тесты намеренно системных частей заменить ожиданием «не выбрано». Сигнатуры экспортёра и мониторинга не менять.

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: все зелёные, кроме предсуществующего `test_prepare_analyze_dahl_uses_dahl_settings`.

- [ ] **Step 6: Commit**

```bash
git add src/intercom_exporter/monitoring.py src/intercom_exporter/exporter.py tests/
git commit -m "feat: exporter and monitoring use meaningful activity (no bare updated_at, no system events)"
```

---

### Task 3: README + проверка на реальных данных без API

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README**

В разделе «Правило отбора и история» заменить описание `updated`/`all`: активностью считаются создание обращения и содержательные части переписки (`comment`, `open`, `note`, `close`); системные события (`bulk_reassignment`, `assignment`, `conversation_attribute_updated_by_admin`, `snoozed`, `timer_unsnooze` и т.п.) и само по себе поле `updated_at` обращение в выборку не включают — это отсекает всплески от массовых админских операций. Синхронно поправить фразу в разделе «Использование» о срезе дня («по `created_at`, `updated_at` или времени любой части переписки» → «по времени создания и содержательных частей переписки»).

- [ ] **Step 2: Проверка на реальных данных (без API)**

Взять готовый экспорт `data/20261026T222417Z_20261102T222417Z/last_week/conversations.json` (полный недельный набор) и пересчитать дневной срез новым правилом локально:

```bash
.venv/Scripts/python.exe - <<'EOF'
import json, sys
sys.path.insert(0, "src")
from intercom_exporter.activity import has_meaningful_activity
from datetime import datetime, timezone
rows = json.load(open("data/20261026T222417Z_20261102T222417Z/last_week/conversations.json", encoding="utf-8"))
end = datetime(2026, 11, 2, 22, 24, 17, tzinfo=timezone.utc).timestamp()
start = end - 86400
selected = [r for r in rows if has_meaningful_activity(r, start, end)]
print("было в last_day:", 3448, "| стало по новому правилу:", len(selected))
EOF
```

Expected: новое число порядка 150–250 (≈107 созданных + ~70 вернувшихся), а не 3448. Записать фактическое число в отчёт.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: meaningful activity selection rule (system events and bare updated_at excluded)"
```

---

## Self-Review

- **Spec coverage:** новое правило (comment/open/note/close + created_at) — Task 1; применение в срезах — Task 2 (monitoring); применение при экспорте — Task 2 (exporter); README — Task 3; проверка эффекта на реальных данных — Task 3 Step 2.
- **Placeholders:** полный код во всех шагах; единственное «найди и обнови» (Task 2 Steps 1/4) неизбежно — состав затронутых тестов зависит от текущего репозитория, implementer обязан перечислить изменённые тесты в отчёте.
- **Type consistency:** `has_meaningful_activity(conversation: dict, start: int, end: int) -> bool` используется в monitoring (через `epoch_bounds()`) и exporter (через `date_range.epoch_bounds()`); `MEANINGFUL_PART_TYPES` — frozenset строк; `_timestamp_epoch` принимает int/float/строки (Unix или ISO) — покрыт тестом `test_unix_timestamps_supported`.

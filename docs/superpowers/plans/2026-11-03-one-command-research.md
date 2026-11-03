# Упрощённый запуск исследования: main.py + research — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Весь цикл исследования сводится к двум командам без путей: `python main.py` (парсинг + очистка + пакет оркестрации) и `python analyze_support.py research` (готовность бандла → оркестрация субагентами → merge-agents → дашборд). Каждый шаг по-прежнему запускается отдельной командой с явными путями.

**Architecture:** Единый детектор последнего прогона `find_latest_run` (каталог в `data/` с `last_week/` и `last_day/`). `run_monitoring` после подготовки анализа автоматически строит пакет оркестрации (флаг `--no-agents`). Команда `research` — композит: проверяет/создаёт бандл, проверяет готовность `result_XX.json` (код возврата 3 = «нужна оркестрация», её в среде Kimi выполняет агент-оркестратор), при готовности мержит с авто-подхватом `agents_work/recommendations.json`. Отдельная команда `render-dashboard` строит дашборд из готового `chatgpt_result.json` без слияния.

**Tech Stack:** Python 3, pytest.

**Файлы:**
- Modify: `src/intercom_exporter/support_analysis.py` (`find_latest_run`, `render_dashboard`, build_parser/main: `merge-agents` опциональные пути, новые `render-dashboard` и `research`)
- Modify: `src/intercom_exporter/monitoring.py` (`run_monitoring`: авто-бандл)
- Modify: `src/intercom_exporter/cli.py` (флаги `--no-agents`, `--agent-workers`, вывод про бандл)
- Modify: `tests/test_support_analysis.py`, `tests/test_agents_orchestration.py`, `tests/test_monitoring.py`
- Modify: `README.md`

**Запуск тестов:** `.venv/Scripts/python.exe -m pytest tests/ -q` (Windows, из корня). Предсуществующий сбой `test_prepare_analyze_dahl_uses_dahl_settings` — игнорировать.

**Контракты существующего кода (проверено):** `prepare_monitoring_analysis(week, day, output) -> {"output_dir": str, ...}`; `prepare_agents_bundle(week, day, output_dir, workers=10) -> manifest`; `merge_agents_results(week, day, output_dir, compare_with=None, recommendations=None)`; `_load_result`, `_validate_result`, `_validate_period_analysis`, `_period_label`, `_default_monitoring_output`, `_read_conversations(allow_empty=True)`, `_build_payload`, `_union_conversations`, `_write_text` — всё в `support_analysis.py`; `build_dashboard_data`, `write_dashboard` в `dashboard.py` (ленивый импорт обязателен: `dashboard` импортирует `_write_text` из `support_analysis`); `run_monitoring(exporter, output_root, *, full_archive, windows, api_version, timezone_name, prepare)`.

---

### Task 1: `find_latest_run` — детектор последнего прогона

**Files:**
- Modify: `src/intercom_exporter/support_analysis.py` (рядом с `_default_monitoring_output`)
- Test: `tests/test_support_analysis.py`

- [ ] **Step 1: Write the failing test**

В `tests/test_support_analysis.py` добавить (импорты `find_latest_run`, `AnalysisError` из `intercom_exporter.support_analysis` уже есть в файле — если нет, добавить):

```python
def _make_run(root, name):
    run = root / name
    (run / "last_week").mkdir(parents=True)
    (run / "last_day").mkdir(parents=True)
    (run / "last_week" / "conversations.json").write_text("[]", encoding="utf-8")
    (run / "last_day" / "conversations.json").write_text("[]", encoding="utf-8")
    return run


def test_find_latest_run_returns_most_recent(tmp_path):
    old = _make_run(tmp_path, "run_old")
    new = _make_run(tmp_path, "run_new")
    os.utime(old / "last_day" / "conversations.json", (1000000000, 1000000000))
    os.utime(new / "last_day" / "conversations.json", (1000000100, 1000000100))

    assert find_latest_run(tmp_path) == new


def test_find_latest_run_ignores_dirs_without_slices(tmp_path):
    run = _make_run(tmp_path, "run_ok")
    (tmp_path / "not_a_run").mkdir()

    assert find_latest_run(tmp_path) == run


def test_find_latest_run_errors_when_no_runs(tmp_path):
    with pytest.raises(AnalysisError, match="нет прогонов"):
        find_latest_run(tmp_path)
```

(В шапке тестового файла убедиться, что импортированы `os` и `pytest`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_support_analysis.py -q -k find_latest_run`
Expected: FAIL — `ImportError: cannot import name 'find_latest_run'`

- [ ] **Step 3: Write the implementation**

В `src/intercom_exporter/support_analysis.py` после `_default_monitoring_output`:

```python
def find_latest_run(data_root: str | Path = "data") -> Path:
    """Каталог последнего прогона мониторинга: содержит срезы last_week и last_day."""
    root = Path(data_root)
    if not root.is_dir():
        raise AnalysisError(f"Каталог данных не найден: {root}")
    candidates = [
        path for path in root.iterdir()
        if path.is_dir()
        and (path / "last_week" / "conversations.json").is_file()
        and (path / "last_day" / "conversations.json").is_file()
    ]
    if not candidates:
        raise AnalysisError(f"В {root} нет прогонов со срезами last_week/last_day")

    def stamp(path: Path) -> float:
        marker = path / "last_day" / "manifest.json"
        try:
            return marker.stat().st_mtime
        except OSError:
            return path.stat().st_mtime

    return max(candidates, key=stamp)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_support_analysis.py -q`
Expected: зелёно (кроме предсуществующего `test_prepare_analyze_dahl_uses_dahl_settings`).

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/support_analysis.py tests/test_support_analysis.py
git commit -m "feat: find_latest_run detects the newest monitoring run in data/"
```

---

### Task 2: `merge-agents` без путей + авто-подхват recommendations.json

**Files:**
- Modify: `src/intercom_exporter/support_analysis.py` (build_parser, ветка `merge-agents`)
- Test: `tests/test_agents_orchestration.py`

- [ ] **Step 1: Write the failing test**

В `tests/test_agents_orchestration.py` добавить:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agents_orchestration.py -q -k "defaults_to_latest or picks_up_agents_work"`
Expected: FAIL — argparse: the following arguments are required: --week/--day.

- [ ] **Step 3: Implementation**

В `build_parser()` у subparser `merge` заменить обязательные `--week`/`--day` на опциональные и добавить `--run`:

```python
    merge.add_argument("--week", help="Исходный conversations.json недельного окна (по умолчанию: последний прогон)")
    merge.add_argument("--day", help="Исходный conversations.json дневного окна (по умолчанию: последний прогон)")
    merge.add_argument("--run", help="Каталог прогона в data/ (по умолчанию: последний); альтернатива --week/--day")
```

Ветка `elif args.command == "merge-agents":` в `main()` — новая логика разрешения путей:

```python
        elif args.command == "merge-agents":
            from .agents_orchestration import merge_agents_results

            week_path, day_path = _resolve_run_paths(args)
            output = Path(args.output) if args.output else _default_monitoring_output(week_path, day_path)
            recommendations = args.recommendations
            if recommendations is None:
                default_rec = output / "agents_work" / "recommendations.json"
                if default_rec.is_file():
                    recommendations = str(default_rec)
            result = merge_agents_results(week_path, day_path, output, compare_with=args.compare_with,
                                          recommendations=recommendations)
            print(
                f"Слито классификаций: {result['unique_conversation_count']}. Результат: {result['result_path']}. "
                f"Отчёт: {result['report_path']}. Дашборд: {result['dashboard_path']}"
            )
            if "comparison_path" in result:
                print(f"Сравнение с предыдущим прогоном: {result['comparison_path']}")
```

И хелпер (рядом с `find_latest_run`):

```python
def _resolve_run_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    """Разрешает пары срезов: явные --week/--day, --run либо последний прогон в data/."""
    provided = [args.week is not None, args.day is not None]
    if any(provided) and not all(provided):
        raise AnalysisError("Указывайте --week и --day вместе, либо используйте --run/последний прогон")
    if all(provided):
        if args.run:
            raise AnalysisError("--run нельзя сочетать с --week/--day")
        return Path(args.week), Path(args.day)
    run = Path(args.run) if args.run else find_latest_run()
    return run / "last_week" / "conversations.json", run / "last_day" / "conversations.json"
```

(В `main()` ошибки `AnalysisError` уже ловятся общим обработчиком — проверить по аналогии с соседними ветками; `argparse` импортирован в модуле.)

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agents_orchestration.py -q` — зелёно.
Run: `.venv/Scripts/python.exe -m pytest tests/ -q` — весь набор зелёный, кроме предсуществующего Dahl.

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/support_analysis.py tests/test_agents_orchestration.py
git commit -m "feat: merge-agents works without paths (latest run, auto recommendations.json)"
```

---

### Task 3: `render-dashboard` — дашборд из готового результата без слияния

**Files:**
- Modify: `src/intercom_exporter/support_analysis.py` (новая функция + subparser + ветка)
- Test: `tests/test_agents_orchestration.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agents_orchestration.py -q -k render_dashboard`
Expected: FAIL — invalid choice: 'render-dashboard'.

- [ ] **Step 3: Implementation**

Функция в `support_analysis.py` (перед `generate_monitoring_report`):

```python
def render_dashboard(
    week_input_path: str | Path,
    day_input_path: str | Path,
    result_path: str | Path,
    output_dir: str | Path | None = None,
    dashboard_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Строит dashboard.html из готового chatgpt_result.json без слияния и отчёта."""
    week_path, day_path = Path(week_input_path), Path(day_input_path)
    week_rows = _read_conversations(week_path, allow_empty=True)
    day_rows = _read_conversations(day_path, allow_empty=True)
    union_rows = _union_conversations(week_rows, day_rows)
    if not union_rows:
        raise AnalysisError("Недельный и дневной срезы не содержат обращений")
    result = _load_result(result_path)
    categories, classifications = _validate_result(union_rows, _build_payload(union_rows), result)
    period_analysis = _validate_period_analysis(result, _build_payload(week_rows), _build_payload(day_rows))
    # Ленивый импорт: dashboard импортирует _write_text из этого модуля — импорт наверху дал бы цикл.
    from .dashboard import build_dashboard_data, write_dashboard

    data = build_dashboard_data(
        week_rows, day_rows, categories, classifications,
        _period_label(week_path) or "Последние 7 дней",
        _period_label(day_path) or "Последний день",
        {
            "overall": result["recommendations"],
            "week": period_analysis["week"]["recommendations"],
            "day": period_analysis["day"]["recommendations"],
        },
    )
    output = Path(output_dir) if output_dir else _default_monitoring_output(week_path, day_path)
    dashboard_path = write_dashboard(data, Path(dashboard_dir) if dashboard_dir else output)
    return {"dashboard_path": str(dashboard_path)}
```

Subparser (после блока `merge`):

```python
    dash = subparsers.add_parser("render-dashboard", help="Построить dashboard.html из готового chatgpt_result.json без слияния")
    dash.add_argument("--week", help="Исходный conversations.json недельного окна (по умолчанию: последний прогон)")
    dash.add_argument("--day", help="Исходный conversations.json дневного окна (по умолчанию: последний прогон)")
    dash.add_argument("--run", help="Каталог прогона в data/ (по умолчанию: последний)")
    dash.add_argument("--result", help="Готовый chatgpt_result.json (по умолчанию: support-analysis рядом с прогоном)")
    dash.add_argument("--output", help="Каталог результатов отчёта")
    dash.add_argument("--dashboard-dir", help="Каталог для dashboard.html (по умолчанию: каталог отчёта)")
```

Ветка в `main()` рядом с `merge-agents`:

```python
        elif args.command == "render-dashboard":
            week_path, day_path = _resolve_run_paths(args)
            result_path = Path(args.result) if args.result else Path(_default_monitoring_output(week_path, day_path)) / "chatgpt_result.json"
            outcome = render_dashboard(week_path, day_path, result_path,
                                       output_dir=args.output, dashboard_dir=args.dashboard_dir)
            print(f"Дашборд: {outcome['dashboard_path']}")
```

- [ ] **Step 4: Run tests**

`.venv/Scripts/python.exe -m pytest tests/ -q` — зелёно (кроме предсуществующего Dahl).

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/support_analysis.py tests/test_agents_orchestration.py
git commit -m "feat: render-dashboard builds dashboard from existing result without merge"
```

---

### Task 4: `main.py` готовит пакет оркестрации автоматически

**Files:**
- Modify: `src/intercom_exporter/monitoring.py` (`run_monitoring`)
- Modify: `src/intercom_exporter/cli.py` (флаги, вывод)
- Test: `tests/test_monitoring.py`

- [ ] **Step 1: Write the failing test**

В `tests/test_monitoring.py` (там уже есть тесты `run_monitoring` с фиктивным экспортёром и `prepare=...` на tmp-пути — по их образцу) добавить:

```python
def test_run_monitoring_creates_agents_bundle_by_default(tmp_path, fake_exporter):
    result = run_monitoring(fake_exporter, tmp_path, prepare=prepare_monitoring_analysis)

    bundle_dir = Path(result["analysis"]["output_dir"]) / "agents_work"
    assert (bundle_dir / "manifest.json").is_file()
    assert result["analysis"]["agents_bundle"]["taxonomy_source"] == "static_v5"


def test_run_monitoring_agents_bundle_disabled(tmp_path, fake_exporter):
    result = run_monitoring(fake_exporter, tmp_path, prepare=prepare_monitoring_analysis, agents=False)

    assert "agents_bundle" not in result["analysis"]
    assert not (Path(result["analysis"]["output_dir"]) / "agents_work").exists()
```

(`fake_exporter` — использовать существующую фикстуру/фабрику тестового файла; если она называется иначе, подставить фактическое имя. Константы для workers: `agents_workers=3` в первом тесте и проверка `manifest["workers"] == 3`.)

- [ ] **Step 2: Run tests to verify they fail**

Expected: FAIL — `TypeError: run_monitoring() got an unexpected keyword argument 'agents'`.

- [ ] **Step 3: Implementation**

В `monitoring.py`:

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
    agents: bool = True,
    agent_workers: int = 10,
) -> dict[str, Any]:
```

и внутри, после `analysis = prepare(...)`:

```python
        if agents and analysis:
            from .agents_orchestration import prepare_agents_bundle

            bundle = prepare_agents_bundle(
                Path(snapshots["last_week"]["output_dir"]) / "conversations.json",
                Path(snapshots["last_day"]["output_dir"]) / "conversations.json",
                Path(analysis["output_dir"]),
                workers=agent_workers,
            )
            analysis = {**analysis, "agents_bundle": bundle}
```

В `cli.py` в `build_parser()` добавить:

```python
    parser.add_argument("--no-agents", action="store_true",
                        help="Мониторинг: не создавать пакет оркестрации субагентов после выгрузки")
    parser.add_argument("--agent-workers", type=int, default=10,
                        help="Число чанков субагентов в пакете оркестрации (2–20, по умолчанию 10)")
```

и в вызов `run_monitoring(...)` передать `agents=not args.no_agents, agent_workers=args.agent_workers`; после печати `Общие файлы для анализа...` добавить:

```python
            if result["analysis"].get("agents_bundle"):
                bundle = result["analysis"]["agents_bundle"]
                print(
                    f"Пакет оркестрации субагентов готов: {bundle['workers']} чанков, "
                    f"таксономия: {bundle['taxonomy_source']}. Манифест: {bundle['work_dir']}/manifest.json."
                )
```

(Авто-бандл не создаётся при `--skip-analysis` / `prepare=None` — условие `agents and analysis` это уже обеспечивает.)

- [ ] **Step 4: Run tests**

`.venv/Scripts/python.exe -m pytest tests/ -q` — зелёно (проверить, что существующие тесты `run_monitoring` не сломались от нового ключа `agents_bundle` в `analysis`; если какой-то ассерт exact-match — обновить осмысленно).

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/monitoring.py src/intercom_exporter/cli.py tests/test_monitoring.py
git commit -m "feat: main.py prepares agents orchestration bundle automatically (--no-agents to skip)"
```

---

### Task 5: `research` — единая вторая команда + README

**Files:**
- Modify: `src/intercom_exporter/support_analysis.py` (subparser + ветка `research`)
- Modify: `README.md`
- Test: `tests/test_agents_orchestration.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: FAIL — invalid choice: 'research'.

- [ ] **Step 3: Implementation**

Subparser после `render-dashboard`:

```python
    research = subparsers.add_parser("research", help="Единый прогон: бандл → ожидание оркестрации → merge → дашборд")
    research.add_argument("--run", help="Каталог прогона в data/ (по умолчанию: последний)")
    research.add_argument("--workers", type=int, default=10,
                          help="Число чанков субагентов, если бандл ещё не создан (2–20)")
    research.add_argument("--compare-with", help="Прошлый chatgpt_result.json для отчёта сравнения")
    research.add_argument("--dashboard-dir", help="Каталог для dashboard.html")
```

Ветка в `main()`:

```python
        elif args.command == "research":
            import json as _json

            from .agents_orchestration import merge_agents_results, prepare_agents_bundle

            run_dir = Path(args.run) if args.run else find_latest_run()
            week_path = run_dir / "last_week" / "conversations.json"
            day_path = run_dir / "last_day" / "conversations.json"
            output = run_dir / "support-analysis"
            if not (output / "agents_work" / "manifest.json").is_file():
                bundle = prepare_agents_bundle(week_path, day_path, output, workers=args.workers)
                print(
                    f"Пакет оркестрации субагентов готов: {bundle['workers']} чанков. "
                    f"Манифест: {bundle['work_dir']}/manifest.json"
                )
            manifest = _json.loads((output / "agents_work" / "manifest.json").read_text(encoding="utf-8-sig"))
            total = len(manifest["chunks"])
            ready = sum(1 for entry in manifest["chunks"] if Path(entry["result_path"]).is_file())
            if ready < total:
                print(
                    f"Оркестрация не завершена: готовы результаты {ready} из {total} воркеров. "
                    "Запустите субагентов по протоколу agents_protocol.md и повторите research."
                )
                return 3
            recommendations = output / "agents_work" / "recommendations.json"
            result = merge_agents_results(
                week_path, day_path, output,
                compare_with=args.compare_with,
                recommendations=str(recommendations) if recommendations.is_file() else None,
            )
            print(
                f"Слито классификаций: {result['unique_conversation_count']}. Отчёт: {result['report_path']}. "
                f"Дашборд: {result['dashboard_path']}"
            )
            if "comparison_path" in result:
                print(f"Сравнение с предыдущим прогоном: {result['comparison_path']}")
            return 0
```

(Если `import json` уже есть наверху модуля — локальный импорт не нужен, использовать его.)

README: в раздел оркестрации добавить после абзаца про `merge-agents`:

```markdown
Единый запуск без путей: `python analyze_support.py research`. Команда находит последний прогон в `data/`, при необходимости создаёт пакет оркестрации, проверяет готовность результатов воркеров (код 3 — «запустите субагентов и повторите») и после оркестрации выполняет слияние с автоподхватом `agents_work/recommendations.json` и построение дашборда. Отдельные шаги: `prepare-monitoring` (очистка), оркестрация по манифесту, `merge-agents`, `render-dashboard` (дашборд из готового `chatgpt_result.json`). После `python main.py` пакет оркестрации создаётся автоматически (отключить: `--no-agents`; число чанков: `--agent-workers N`).
```

- [ ] **Step 4: Run tests**

`.venv/Scripts/python.exe -m pytest tests/ -q` — зелёно (кроме предсуществующего Dahl).

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/support_analysis.py tests/test_agents_orchestration.py README.md
git commit -m "feat: research command — bundle, orchestration gate, merge and dashboard without paths"
```

---

## Self-Review

- **Spec coverage:** main.py парсит и чистит + готовит бандл — Task 4; вторая команда без путей (research: оркестрация-гейт → merge → дашборд) — Task 5; указание конкретных папок — `--run`/`--week`/`--day` везде (Tasks 2, 3, 5); отдельный запуск парсинга — `main.py` как есть; отдельно очистка — `prepare-monitoring`; отдельно оркестрация — по манифесту агентом (код 3 research направляет); отдельно merge — Task 2; отдельно дашборд при наличии отчёта — Task 3.
- **Placeholders:** Task 4 Step 1 ссылается на существующую фикстуру fake_exporter с оговоркой подставить фактическое имя — реальное неизбежное «найди в файле», остальное — полный код.
- **Type consistency:** `find_latest_run(data_root="data") -> Path`; `_resolve_run_paths(args) -> (Path, Path)`; `render_dashboard(week, day, result, output_dir=None, dashboard_dir=None) -> {"dashboard_path": str}`; `run_monitoring(..., agents=True, agent_workers=10)`; `research` возвращает 0/3; `agents_bundle` добавляется в dict `analysis` только при включённом режиме — CLI печатает через `.get()`.
- **Честная оговорка про оркестрацию:** Python-команда не может поднять субагентов (это поведение агента Kimi); `research` кодирует контракт «не готово → код 3 + инструкция», а в среде Kimi агент-оркестратор выполняет воркеров между двумя запусками research. Это зафиксировано в README.

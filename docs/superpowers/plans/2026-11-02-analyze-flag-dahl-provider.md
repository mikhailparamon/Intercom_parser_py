# Флаг `--analyze` и провайдер Dahl — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Автоматический AI-анализ больше не запускается по умолчанию: без флага готовятся только файлы для ручного анализа. Анализ включается флагом `--analyze openrouter|dahl`. Добавляется провайдер Dahl (`https://inference.dahl.global/v1`, OpenAI-совместимый, модель по умолчанию `MiniMaxAI/MiniMax-M2.7`).

**Architecture:** `openrouter.py` параметризуется провайдером через keyword-параметры `base_url` и `provider_label` (подписи `run_standard_analysis`/`run_monitoring_analysis` сохраняют позиционные `api_key, model, workers` — существующие тесты не ломаются). Настройки Dahl грузятся из `.env` (`DAHL_API_KEY`, `DAHL_MODEL`, `DAHL_WORKERS`) параллельно OpenRouter. CLI маппит `--analyze` на настройки провайдера.

**Tech Stack:** Python 3.11+, pytest, argparse, requests.

**Решения (подтверждены пользователем):**
1. Флаг `--analyze PROVIDER` (openrouter|dahl); без флага — только ручная подготовка, даже если ключи заданы.
2. Dahl: `DAHL_API_KEY` + `DAHL_MODEL` (дефолт `MiniMaxAI/MiniMax-M2.7`) + `DAHL_WORKERS` (дефолт 3, 1–10).
3. `--analyze X` без соответствующего ключа → `parser.error` (exit 2).
4. Dahl endpoint OpenAI-совместимый (проверено: `GET /v1/models` возвращает список с `MiniMaxAI/MiniMax-M2.7`), chat completions: `https://inference.dahl.global/v1/chat/completions`.

---

### Task 1: Настройки Dahl в `config.py` и `.env.example`

**Files:**
- Modify: `src/intercom_exporter/config.py`
- Modify: `.env.example`
- Test: `tests/test_openrouter.py` (там уже лежат тесты настроек)

- [ ] **Step 1: Write the failing tests**

Добавить в `tests/test_openrouter.py` рядом с `test_load_openrouter_settings_is_optional_and_has_default_model`:

```python
def test_load_dahl_settings_is_optional_and_has_default_model(tmp_path, monkeypatch):
    from intercom_exporter.config import DEFAULT_DAHL_MODEL, load_dahl_settings

    monkeypatch.delenv("DAHL_API_KEY", raising=False)
    monkeypatch.delenv("DAHL_MODEL", raising=False)
    monkeypatch.delenv("DAHL_WORKERS", raising=False)
    assert load_dahl_settings(tmp_path / ".env") is None

    env_file = tmp_path / ".env"
    env_file.write_text("DAHL_API_KEY=test-key\n", encoding="utf-8")
    settings = load_dahl_settings(env_file)
    assert settings is not None
    assert settings.api_key == "test-key"
    assert settings.model == DEFAULT_DAHL_MODEL == "MiniMaxAI/MiniMax-M2.7"
    assert settings.workers == 3


def test_load_config_exposes_dahl_settings(tmp_path, monkeypatch):
    from intercom_exporter.config import load_config

    monkeypatch.delenv("DAHL_API_KEY", raising=False)
    monkeypatch.delenv("DAHL_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "INTERCOM_ACCESS_TOKEN=token\nDAHL_API_KEY=dahl-key\nDAHL_MODEL=vendor/model\nDAHL_WORKERS=5\n",
        encoding="utf-8",
    )
    config = load_config(env_file)
    assert config.dahl_api_key == "dahl-key"
    assert config.dahl_model == "vendor/model"
    assert config.dahl_workers == 5

    env_file.write_text("INTERCOM_ACCESS_TOKEN=token\n", encoding="utf-8")
    monkeypatch.delenv("DAHL_API_KEY", raising=False)
    config = load_config(env_file)
    assert config.dahl_api_key is None
```

(Посмотреть, как написан существующий `test_load_openrouter_settings_is_optional_and_has_default_model`, и повторить его стиль monkeypatch/env-файла.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_openrouter.py -k dahl -v`
Expected: FAIL (`ImportError: cannot import name 'load_dahl_settings'`)

- [ ] **Step 3: Implement**

В `src/intercom_exporter/config.py`:

1. После констант OpenRouter добавить:

```python
DEFAULT_DAHL_MODEL = "MiniMaxAI/MiniMax-M2.7"
DEFAULT_DAHL_WORKERS = 3
```

2. После `load_openrouter_settings` добавить:

```python
@dataclass(frozen=True)
class DahlSettings:
    api_key: str
    model: str
    workers: int = DEFAULT_DAHL_WORKERS


def load_dahl_settings(env_file: str | Path | None = None) -> DahlSettings | None:
    load_dotenv(dotenv_path=env_file, override=False)
    api_key = os.getenv("DAHL_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.getenv("DAHL_MODEL", "").strip() or DEFAULT_DAHL_MODEL
    try:
        workers = int(os.getenv("DAHL_WORKERS", str(DEFAULT_DAHL_WORKERS)))
    except ValueError as exc:
        raise ValueError("DAHL_WORKERS должен быть целым числом от 1 до 10.") from exc
    if not 1 <= workers <= 10:
        raise ValueError("DAHL_WORKERS должен быть целым числом от 1 до 10.")
    return DahlSettings(api_key, model, workers)
```

3. В `Config` добавить поля:

```python
    dahl_api_key: str | None = None
    dahl_model: str = DEFAULT_DAHL_MODEL
    dahl_workers: int = DEFAULT_DAHL_WORKERS
```

4. В `load_config` после `openrouter = load_openrouter_settings(env_file)` добавить `dahl = load_dahl_settings(env_file)` и в `Config(...)` — `dahl.api_key if dahl else None, dahl.model if dahl else DEFAULT_DAHL_MODEL, dahl.workers if dahl else DEFAULT_DAHL_WORKERS`.

5. В `.env.example` добавить строки:

```
DAHL_API_KEY=
DAHL_MODEL=MiniMaxAI/MiniMax-M2.7
DAHL_WORKERS=3
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_openrouter.py -v` и полный набор `.venv/Scripts/python.exe -m pytest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/config.py tests/test_openrouter.py .env.example
git commit -m "feat(config): Dahl provider settings from .env"
```

---

### Task 2: Провайдеро-независимый `openrouter.py`

**Files:**
- Modify: `src/intercom_exporter/openrouter.py`
- Test: `tests/test_openrouter.py`

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_openrouter.py`:

```python
def test_analysis_supports_dahl_endpoint_and_label(tmp_path, monkeypatch):
    import intercom_exporter.openrouter as openrouter
    from intercom_exporter.openrouter import DAHL_URL

    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((base_url := None, url := None))  # placeholder, replaced below
        raise AssertionError("unreachable")

    requests_calls = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json_module.dumps(valid)}}]}

    # --- actual test body ---
    import json as json_module

    source = tmp_path / "conversations.json"
    source.write_text(json_module.dumps([
        {"id": "1", "created_at": 1793000000, "conversation_parts": {"conversation_parts": []}}
    ]), encoding="utf-8")
    from intercom_exporter.support_analysis import prepare_analysis
    prepared = prepare_analysis(source)

    valid = {
        "complete": True,
        "conversation_count": 1,
        "dataset_fingerprint": prepared_payload_fingerprint(prepared),
        "categories": [{"name": "cat", "description": "d"}],
        "classifications": [{
            "id": "1", "category": "cat", "subcategory": "sub",
            "problem_summary": "p", "severity": None, "severity_reason": "r",
            "confidence": "low", "evidence": "e",
        }],
        "executive_summary": "s",
        "recommendations": ["r"],
    }

    def fake_request(api_key, model, prompt, user_content, **kwargs):
        requests_calls.append((api_key, model))
        return valid

    monkeypatch.setattr(openrouter, "_request_result", fake_request)
    result = run_standard_analysis(
        source, prepared["output_dir"], "dahl-secret", "MiniMaxAI/MiniMax-M2.7",
        base_url=DAHL_URL, provider_label="Dahl",
    )
    assert result["result_path"].endswith("chatgpt_result.json")
    assert requests_calls and requests_calls[0][0] == "dahl-secret"
```

ВНИМАНИЕ: приведённый выше набросок содержит placeholder-строки (`fake_post`) — при реализации удалить их и оставить чистый тест. Для `prepared_payload_fingerprint` посмотреть существующий helper в `tests/test_openrouter.py` (`valid_result(rows)` и фикстуры вокруг строки 98-130) и переиспользовать их вместо изобретения новых. Итоговый тест: один вызов `run_standard_analysis(..., base_url=DAHL_URL, provider_label="Dahl")`, проверка результата и того, что в `_request_result` ушли ключ и модель Dahl.

Дополнительно добавить проверку URL: monkeypatch `requests.post` (не `_request_result`) для `_request_result`-уровня:

```python
def test_request_result_uses_given_base_url(monkeypatch):
    import intercom_exporter.openrouter as openrouter

    seen = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{\"ok\": true}"}}]}

    def fake_post(url, **kwargs):
        seen["url"] = url
        return FakeResponse()

    monkeypatch.setattr(openrouter.requests, "post", fake_post)
    assert openrouter._request_result("k", "m", "p", "c", base_url=openrouter.DAHL_URL, provider_label="Dahl") == {"ok": True}
    assert seen["url"] == "https://inference.dahl.global/v1/chat/completions"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_openrouter.py -k "dahl or base_url" -v`
Expected: FAIL (нет `DAHL_URL`, неожиданные kwargs)

- [ ] **Step 3: Implement**

В `src/intercom_exporter/openrouter.py`:

1. Константы:

```python
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DAHL_URL = "https://inference.dahl.global/v1/chat/completions"
OPENROUTER_LABEL = "OpenRouter"
```

2. `_decode_model_result(response_body, provider_label: str = OPENROUTER_LABEL)` — все строки "OpenRouter вернул…" заменить на f-строки с `{provider_label}`.
3. `_request_result(api_key, model, prompt, user_content, *, base_url: str = OPENROUTER_URL, provider_label: str = OPENROUTER_LABEL)` — `requests.post(base_url, ...)`; тексты ошибок через `{provider_label}`; вызов `_decode_model_result(body, provider_label)`.
4. `_prepared_payload`: текст ошибки «для OpenRouter» → «для AI-анализа».
5. `_synthesis_result(..., api_key, model, ...)` — добавить keyword-only `base_url: str = OPENROUTER_URL, provider_label: str = OPENROUTER_LABEL`, прокинуть в `_request_result`; тексты «Итоговый синтез OpenRouter…» → `{provider_label}`.
6. `_classify_batches(...)` — добавить keyword-only `base_url` и `provider_label`; прокинуть в `_request_result` и `_synthesis_result`; print-сообщения `f"{provider_label}: отправка пакета …"` и т.д.; в cache_key dict добавить `"provider": provider_label`.
7. `run_standard_analysis(source_path, output_dir, api_key, model, workers=DEFAULT_OPENROUTER_WORKERS, *, base_url=OPENROUTER_URL, provider_label=OPENROUTER_LABEL)` и аналогично `run_monitoring_analysis` — прокинуть в `_classify_batches`; тексты «Ответ OpenRouter не прошел проверку…» → `{provider_label}`.
8. Класс `OpenRouterError` НЕ переименовывать.

- [ ] **Step 4: Fix existing test fakes**

Существующие тесты monkeypatch'ят `_request_result` фейками с сигнатурой `(api_key, model, prompt, user_content)` — теперь реальный код передаёт ещё keyword-аргументы. Обновить такие фейки, добавив `**_kwargs` (найти все: строки ~153, ~184, ~222, ~237, ~326). Запустить весь файл.

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_openrouter.py -v` и полный набор
Expected: PASS (поведение OpenRouter по умолчанию неизменно — все старые тесты зелёные)

- [ ] **Step 6: Commit**

```bash
git add src/intercom_exporter/openrouter.py tests/test_openrouter.py
git commit -m "feat(analysis): parameterize provider endpoint and label, add Dahl URL"
```

---

### Task 3: CLI — флаг `--analyze`, ручной режим по умолчанию

**Files:**
- Modify: `src/intercom_exporter/cli.py`
- Test: `tests/test_config_cli.py`

- [ ] **Step 1: Write/replace failing tests**

В `tests/test_config_cli.py`:

1. Обновить `test_monitoring_cli_runs_one_openrouter_analysis_for_week_and_day`: вызов `cli.main([])` → `cli.main(["--analyze", "openrouter"])` (поведение автоанализа теперь за флагом).
2. Обновить стандартный тест автоанализа (~строка 152): `cli.main(["--standard", ...])` → добавить `"--analyze", "openrouter"`.
3. Новые тесты:

```python
def test_cli_without_analyze_flag_skips_auto_analysis_even_with_key(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(
        access_token="token", api_version="2.17", openrouter_api_key="router-key", openrouter_model="m",
    ))
    monkeypatch.setattr(cli, "IntercomClient", lambda *args: object())
    monkeypatch.setattr(cli, "ConversationExporter", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "run_monitoring", lambda *args, **kwargs: {
        "export": {"status": "success", "found_count": 1, "exported_count": 1, "error_count": 0, "elapsed_seconds": 1},
        "snapshots": {
            "last_week": {"conversation_count": 1, "output_dir": str(tmp_path / "week")},
            "last_day": {"conversation_count": 1, "output_dir": str(tmp_path / "day")},
        },
        "analysis": {"output_dir": str(tmp_path / "support-analysis")},
    })
    monkeypatch.setattr(cli, "run_monitoring_analysis", lambda *args, **kwargs: calls.append(args))

    assert cli.main([]) == 0
    assert calls == []
    assert "ручного анализа" in capsys.readouterr().out


def test_cli_analyze_dahl_uses_dahl_settings(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from intercom_exporter.openrouter import DAHL_URL
    calls = []
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(
        access_token="token", api_version="2.17",
        dahl_api_key="dahl-key", dahl_model="MiniMaxAI/MiniMax-M2.7", dahl_workers=2,
    ))
    monkeypatch.setattr(cli, "IntercomClient", lambda *args: object())
    monkeypatch.setattr(cli, "ConversationExporter", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "run_monitoring", lambda *args, **kwargs: {
        "export": {"status": "success", "found_count": 1, "exported_count": 1, "error_count": 0, "elapsed_seconds": 1},
        "snapshots": {
            "last_week": {"conversation_count": 1, "output_dir": str(tmp_path / "week")},
            "last_day": {"conversation_count": 1, "output_dir": str(tmp_path / "day")},
        },
        "analysis": {"output_dir": str(tmp_path / "support-analysis")},
    })
    monkeypatch.setattr(cli, "run_monitoring_analysis", lambda *args, **kwargs: (
        calls.append(kwargs) or {"result_path": "r", "report_path": "rep", "classifications_path": "csv"}
    ))

    assert cli.main(["--analyze", "dahl"]) == 0
    assert calls[0]["api_key"] == "dahl-key"
    assert calls[0]["model"] == "MiniMaxAI/MiniMax-M2.7"
    assert calls[0]["workers"] == 2
    assert calls[0]["base_url"] == DAHL_URL
    assert calls[0]["provider_label"] == "Dahl"
    assert "Dahl" in capsys.readouterr().out


def test_cli_analyze_without_provider_key_is_rejected(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(
        access_token="token", api_version="2.17",
    ))
    monkeypatch.setattr(cli, "IntercomClient", lambda *args: object())
    monkeypatch.setattr(cli, "ConversationExporter", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "run_monitoring", lambda *args, **kwargs: {
        "export": {"status": "success", "found_count": 1, "exported_count": 1, "error_count": 0, "elapsed_seconds": 1},
        "snapshots": {
            "last_week": {"conversation_count": 1, "output_dir": str(tmp_path / "week")},
            "last_day": {"conversation_count": 1, "output_dir": str(tmp_path / "day")},
        },
        "analysis": {"output_dir": str(tmp_path / "support-analysis")},
    })

    with pytest.raises(SystemExit) as error:
        cli.main(["--analyze", "dahl"])
    assert error.value.code == 2
    assert "DAHL_API_KEY" in capsys.readouterr().err
```

(Точная форма kwargs/позиционных аргументов в `run_monitoring_analysis` зависит от реализации — адаптировать asserts под выбранную сигнатуру, но проверить ключ, модель, workers, base_url, label.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config_cli.py -v`
Expected: FAIL (`unrecognized arguments: --analyze`, старые автоанализ-тесты падают)

- [ ] **Step 3: Implement**

В `src/intercom_exporter/cli.py`:

1. Импорты: `from .openrouter import DAHL_URL, OPENROUTER_URL, OpenRouterError, run_monitoring_analysis, run_standard_analysis`.
2. В `build_parser()` добавить:

```python
    parser.add_argument("--analyze", choices=("openrouter", "dahl"),
                        help="Автоматический AI-анализ после экспорта через выбранный провайдер (без флага — только подготовка файлов для ручного анализа)")
```

3. Хелпер в cli.py:

```python
def _analysis_kwargs(args, config, parser) -> dict | None:
    """Настройки автоанализа или None, если анализ не запрошен."""
    if not args.analyze:
        return None
    if args.analyze == "openrouter":
        api_key = getattr(config, "openrouter_api_key", None)
        model = getattr(config, "openrouter_model", "anthropic/claude-sonnet-4.5")
        workers = getattr(config, "openrouter_workers", 3)
        base_url, label, missing = OPENROUTER_URL, "OpenRouter", "OPENROUTER_API_KEY"
    else:
        api_key = getattr(config, "dahl_api_key", None)
        model = getattr(config, "dahl_model", "MiniMaxAI/MiniMax-M2.7")
        workers = getattr(config, "dahl_workers", 3)
        base_url, label, missing = DAHL_URL, "Dahl", "DAHL_API_KEY"
    if not api_key:
        parser.error(f"--analyze {args.analyze}: не задан {missing} в .env")
    return {"api_key": api_key, "model": model, "workers": workers,
            "base_url": base_url, "provider_label": label}
```

4. В обеих ветках заменить логику автоанализа: `analysis_kwargs = _analysis_kwargs(args, config, parser)` сразу после `load_config`; блок `if getattr(config, "openrouter_api_key", None):` → `if analysis_kwargs:`, вызов `run_monitoring_analysis(week, day, out, analysis_kwargs["api_key"], analysis_kwargs["model"], analysis_kwargs["workers"], base_url=analysis_kwargs["base_url"], provider_label=analysis_kwargs["provider_label"])` (аналогично `run_standard_analysis`); сообщение «Выполняется анализ через OpenRouter...» → `f"Выполняется анализ через {analysis_kwargs['provider_label']}..."`; сообщение «OPENROUTER_API_KEY не задан; ...» → «Автоматический анализ отключён (включить: --analyze openrouter|dahl); подготовлены файлы для ручного анализа в ChatGPT.»

Внимание: `--skip-analysis` и `--analyze` вместе — `--skip-analysis` побеждает (анализ не готовится и не запускается); явной ошибки не нужно.

- [ ] **Step 4: Run tests + smoke**

Run: `.venv/Scripts/python.exe -m pytest` (полный набор) и `.venv/Scripts/python.exe main.py --dry-run --analyze dahl` (dry-run не трогает анализ, но флаг должен парситься)
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/cli.py tests/test_config_cli.py
git commit -m "feat(cli): --analyze flag with openrouter/dahl providers, manual analysis by default"
```

---

### Task 4: README и `.env.example` документация

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update analysis sections**

Найти в README абзац про автоматический OpenRouter-анализ (ищется по «OPENROUTER_API_KEY»). Переписать: анализ по умолчанию выключен, файлы готовятся для ручного анализа; автоанализ включается `--analyze openrouter` (нужен `OPENROUTER_API_KEY`) или `--analyze dahl` (нужен `DAHL_API_KEY`, endpoint `https://inference.dahl.global/v1`, модель по умолчанию `MiniMaxAI/MiniMax-M2.7`, настраивается `DAHL_MODEL`, параллелизм `DAHL_WORKERS`).

- [ ] **Step 2: Options table**

Добавить строку:

```markdown
| `--analyze {openrouter,dahl}` | Автоматический AI-анализ после экспорта через выбранный провайдер; без флага готовятся только файлы для ручного анализа. |
```

- [ ] **Step 3: Env documentation**

Обновить описание переменных `.env`: добавить `DAHL_API_KEY`, `DAHL_MODEL`, `DAHL_WORKERS`.

- [ ] **Step 4: Verify + commit**

Run: `.venv/Scripts/python.exe -m pytest` (124+ passed, документация не ломает тесты)

```bash
git add README.md
git commit -m "docs: --analyze flag and Dahl provider"
```

---

## Self-Review пройден

- **Spec coverage:** (1) автоанализ выключен по умолчанию → Task 3; (2) флаг-провайдер → Task 3; (3) Dahl endpoint+модель → Task 1, 2; (4) env-настройки Dahl → Task 1; (5) ошибка при отсутствии ключа → Task 3.
- **Placeholder scan:** тест Task 2 содержит помеченный набросок с явной инструкцией переиспользовать существующие фикстуры — осознанно, так как точная форма фикстур известна только при чтении файла; остальной код полный.
- **Type consistency:** `base_url`, `provider_label`, `DAHL_URL`, `load_dahl_settings`, `DahlSettings`, поля `dahl_*` в Config — единообразны во всех задачах.

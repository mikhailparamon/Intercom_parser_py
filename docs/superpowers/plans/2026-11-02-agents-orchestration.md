# Фиксированная таксономия v5 + оркестрация субагентов по умолчанию — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Промпт анализа переходит на фиксированную таксономию из 10 категорий с правилами границ; (2) параллельная классификация субагентами становится режимом по умолчанию: `prepare-monitoring` создаёт пакет оркестрации (чанки + статическая таксономия + манифест + протокол), `merge-agents` валидирует результаты и автоматически строит report.md + dashboard.html через существующий `generate_monitoring_report`, с опциональным `--compare-with`.

**Architecture:** Таксономия — статическая часть промпт-шаблона и Python-константа (единый источник: `agents_orchestration.TAXONOMY`, синхронизирован с шаблоном); сравнение со старыми 13-категорийными прогонами не поддерживается (чистый переход). Оркестрация — поведение рантайма агента: Python детерминированно готовит пакет и сливает результаты; протокол исполнителя (`agents_protocol.md`) отдельно от PROMPT.md.

**Tech Stack:** Python 3, `intercom_exporter`, pytest.

**Файлы:**
- Modify: `src/intercom_exporter/support_analysis_prompt_template.md` (таксономия, правила границ, переименования)
- Modify: `src/intercom_exporter/dashboard.py` (KPI под новую категорию)
- Create: `src/intercom_exporter/agents_orchestration.py`
- Create: `src/intercom_exporter/agents_protocol.md`
- Modify: `src/intercom_exporter/support_analysis.py` (`build_parser`, `main`)
- Modify: `tests/test_dashboard.py`; Create: `tests/test_agents_orchestration.py`
- Modify: `README.md`

**Запуск тестов:** `.venv/Scripts/python.exe -m pytest tests/ -q` (из корня проекта, Windows, `.venv/Scripts/python.exe`).

---

### Task 1: Фиксированная таксономия v5 в промпт-шаблоне и KPI дашборда

**Files:**
- Modify: `src/intercom_exporter/support_analysis_prompt_template.md`
- Modify: `src/intercom_exporter/dashboard.py`
- Modify: `tests/test_dashboard.py`

Контекст: шаблон сейчас содержит 13 категорий (раздел 3, строки 42–58), таблицу приоритетов (раздел 4, строки 60–86) и упоминания «Недостаточно данных» в разделах 5, 6, 8–11. Переходим на 10 фиксированных категорий заказчика. `_monitoring_prompt` в support_analysis.py вырезает срез от `## 1.` до `## 8.` — нумерацию разделов и эти заголовки не трогаем.

- [ ] **Step 1: Обновить раздел 3 шаблона**

Заменить раздел `## 3. Единая стабильная таксономия` (строки 42–58) на:

```markdown
## 3. Фиксированная таксономия (10 категорий)

Применяй ровно эти **10 категорий** ко всему набору. Запрещено создавать новые категории, переименовывать существующие и менять названия между партиями. Подкатегории свободные: короткое уточнение по содержанию обращения, согласованное между партиями. Все 10 категорий включи в массив `categories` результата с описаниями из этого раздела.

| № | Категория | Что входит |
|---:|---|---|
| 1 | **Проблемы подключения VPN** | VPN не подключается, соединение не устанавливается, обрывается или постоянно переподключается. |
| 2 | **Доступ к сайтам и сервисам** | VPN подключён, но не открываются сайты, банки, Госуслуги, мобильные приложения, телефония и другие ресурсы. |
| 3 | **Скорость и качество соединения** | Низкая скорость, задержки, зависания, нестабильная работа и проблемы с качеством передачи данных. |
| 4 | **Ключи и конфигурации** | Ключ не пришёл, не скачивается, не импортируется, не работает; требуется повторная выдача, замена ключа или сервера. |
| 5 | **Оплата и управление подпиской** | Оплата не проходит или не распознаётся, неверное списание, возврат денег, отмена, продление и изменение подписки. |
| 6 | **Установка и настройка** | Установка и обновление OpenVPN, настройка приложения или расширения, ошибки запуска, конфликты программ. |
| 7 | **Совместимость и ограничения** | Поддержка устройств, операционных систем, роутеров, протоколов, стран, IP-адресов и особенностей подключения. |
| 8 | **Авторизация и доступ к аккаунту** | Не приходит код подтверждения, ошибка входа, проблемы с авторизацией через браузерное расширение или личный кабинет. |
| 9 | **Тарифы и вопросы о сервисе** | Стоимость, выбор тарифа, условия использования, возможности сервиса, организационные вопросы и коммуникация с поддержкой. |
| 10 | **Прочие и неклассифицируемые обращения** | Автоматические уведомления, спам, предложения о сотрудничестве, сообщения не по теме и обращения, в которых недостаточно информации. |

### Правила границ между категориями

1. VPN не подключается — категория 1. Если VPN подключён, но не открывается конкретный сайт, — категория 2.
2. VPN подключён, но работает медленно — категория 3. Не относите любую нестабильность к проблеме подключения.
3. Ключ не пришёл или не импортируется — категория 4. Если ключ импортирован, но VPN не соединяется, — категория 1.
4. Не приходит код авторизации — категория 8. Код входа в расширение не путай с конфигурационным VPN-ключом.
5. Оплата и подписка — категория 5. Возврат денег за подписку, отмена подписки и обычная ошибка списания остаются разными подкатегориями внутри неё.
6. Не работает на конкретном устройстве или в стране — категория 7, если основная суть обращения в совместимости или ограничении поддержки. Если VPN работает, но не открывает конкретный ресурс, — категория 2.
```

- [ ] **Step 2: Обновить раздел 4 (таблица приоритетов)**

Заменить таблицу в разделе `## 4. Приоритеты между близкими темами` на версию с новыми именами категорий:

```markdown
| Ситуация из клиентской переписки | Выбор |
|---|---|
| «Оплатил, а ключ не пришёл» / «пришлите ещё раз .ovpn» | **Ключи и конфигурации**; если только «ничего не пришло» без предмета — используй другие сообщения и заголовок, при недостатке — категория 10 с осторожностью |
| «Не могу загрузить / импортировать ключ» | **Ключи и конфигурации** (подкатегория вида «Импорт ключа») |
| «Ключ загрузил, но VPN не соединяется» | **Проблемы подключения VPN** |
| «Оплатил, а приложение пишет, что подписка не активна» | **Оплата и управление подпиской** (подкатегория вида «Оплата не распознана») |
| «После оплаты когда активируется подписка?» | **Оплата и управление подпиской** — вопрос о статусе, а не доказанный платёжный сбой |
| «Хочу отменить подписку», `cancel my subscription` | **Оплата и управление подпиской** (подкатегория «Отмена подписки») |
| «Верните оплату за подписку» | **Оплата и управление подпиской** (подкатегория «Возврат денег»); возврат не приписывай отмене, если отмену не просили |
| «Не приходит код подтверждения», «код неверный» | **Авторизация и доступ к аккаунту**; не конфигурационный ключ |
| «VPN подключён, но не открывается банк / Госуслуги / Иви» | **Доступ к сайтам и сервисам** |
| «VPN вообще не подключается» | **Проблемы подключения VPN**; не «Доступ к сайтам» |
| «Звонки заикаются / звук прерывается» | **Скорость и качество соединения** |
| «IP-телефония вообще не работает / нет вызовов» | Не назначай автоматически низкую скорость: проверь, речь о доступности сервиса (категория 2), совместимости (7) или соединении (1); выбирай подтверждённую тему |
| «Как установить VPN на Mac?» | **Установка и настройка** |
| «Поддерживается ли VPN на телевизоре / TCP / в Туркменистане?» | **Совместимость и ограничения** |
| «Есть ли пробный период / сколько стоит?» | **Тарифы и вопросы о сервисе** |
| «Почему поддержка не отвечает?» после конкретной жалобы на VPN | Основная категория технической жалобы; **Тарифы и вопросы о сервисе** — только если коммуникация с поддержкой самостоятельный предмет обращения |
| Рекламное предложение, спам, автоответ «в отпуске», пустая переписка | **Прочие и неклассифицируемые обращения** |
```

Абзац после таблицы («Запрос на возврат по подписке не обязательно означает желание отмены...») сохранить, заменив «подкатегорию „Возврат по подписке"» на «подкатегорию возврата денег в категории 5».

- [ ] **Step 3: Переименовать «Недостаточно данных» в категорию 10 по всему шаблону**

- Раздел 5: заголовок `## 5. Как безопасно уменьшать «Недостаточно данных»` → `## 5. Как безопасно уменьшать долю неклассифицируемых`; каждое «Недостаточно данных» в тексте раздела → «Прочие и неклассифицируемые обращения»; в особом правиле очереди «Подключение VPN / Другое подключение» → «Проблемы подключения VPN» (подкатегория вида «Другое подключение»). Подкатегории второго прохода («Нет содержательного текста», «Неясный объект проблемы», «Контекст отсутствует», «Только вложение без описания») сохранить — теперь это подкатегории категории 10.
- Раздел 6: «Для „Недостаточно данных" всегда `low`» → «Для категории 10 всегда `low`»; пример в `severity_reason` «Недостаточно данных о влиянии на клиента» — оставить (это фраза-обоснование, не имя категории).
- Раздел 8: в примере JSON `"category": "Подключение VPN"` → `"category": "Проблемы подключения VPN"`, `"subcategory": "Другое подключение"` — оставить.
- Раздел 9: «даже когда категория — „Недостаточно данных"» → «даже когда категория — „Прочие и неклассифицируемые обращения"».
- Раздел 10: пункты 3 и 5 — «включая „Недостаточно данных" и нецелевые обращения» → «включая категорию 10»; «число и доля „Недостаточно данных"» → «число и доля категории 10 с разбивкой по подкатегориям».
- Раздел 12: «уменьшение „Недостаточно данных"» → «доля категории 10».
- Раздел 11 финальная проверка: «количество „Недостаточно данных"» → «число обращений категории 10».

- [ ] **Step 4: KPI дашборда под новую категорию**

В `src/intercom_exporter/dashboard.py` в `build_dashboard_data` заменить эвристику:

```python
    cancel_week = sum(
        entry["count"] for entry in week_categories
        if "отмен" in entry["name"].casefold() or "возврат" in entry["name"].casefold()
    )
```

на точное совпадение с категорией 5:

```python
    billing_week = sum(
        entry["count"] for entry in week_categories
        if entry["name"] == "Оплата и управление подпиской"
    )
```

Ключ `"cancel_week"` в `kpis` переименовать в `"billing_week"`. В `_TEMPLATE` JS-массив kpis: заменить `["Подписка и отмена (неделя)", DATA.kpis.cancel_week, ""]` на `["Оплата и подписка (неделя)", DATA.kpis.billing_week, ""]`.

В `tests/test_dashboard.py`: в CATEGORIES заменить `{"name": "Подписка и отмена", ...}` на `{"name": "Оплата и управление подпиской", "description": "Оплата и подписка"}`; тест `test_build_dashboard_data_counts_windows_categories_and_severity`: `data["kpis"]["cancel_week"] == 0` → `data["kpis"]["billing_week"] == 0`; тест `test_cancel_week_counts_categories_with_cancellation_in_name` переименовать в `test_billing_week_counts_billing_category` и переписать:

```python
def test_billing_week_counts_billing_category():
    data = build_dashboard_data(_week_rows(), _day_rows(), CATEGORIES, [
        _classification("w1", "Оплата и управление подпиской", "Отмена подписки", None),
        _classification("w2", "Оплата", "Карта отклонена", None),
        _classification("d1", "Оплата и управление подпиской", "Возврат денег", None),
    ], "w", "d")
    assert data["kpis"]["billing_week"] == 2
```

(Категория "Оплата" в фикстуре намеренно похожа — проверяет, что совпадение точное, не по подстроке.)

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: все зелёные, кроме известного предсуществующего `test_prepare_analyze_dahl_uses_dahl_settings`.

- [ ] **Step 6: Commit**

```bash
git add src/intercom_exporter/support_analysis_prompt_template.md src/intercom_exporter/dashboard.py tests/test_dashboard.py
git commit -m "feat: fixed 10-category taxonomy v5 in analysis prompt, billing KPI"
```

---

### Task 2: Модуль оркестрации `agents_orchestration.py`

**Files:**
- Create: `src/intercom_exporter/agents_orchestration.py`
- Create: `src/intercom_exporter/agents_protocol.md`
- Test: `tests/test_agents_orchestration.py`

- [ ] **Step 1: Write the failing test**

Создать `tests/test_agents_orchestration.py`:

```python
import json
from pathlib import Path

import pytest

from intercom_exporter.agents_orchestration import (
    TAXONOMY,
    merge_agents_results,
    prepare_agents_bundle,
)
from intercom_exporter.support_analysis import AnalysisError


def _write_conversations(directory, ids):
    directory.mkdir(parents=True)
    rows = [{"id": cid, "created_at": "2026-11-01T10:00:00Z", "updated_at": "2026-11-01T10:00:00Z",
             "source": {"body": f"Клиент {cid}: не работает VPN"},
             "conversation_parts": {"conversation_parts": []}} for cid in ids]
    path = directory / "conversations.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def test_prepare_agents_bundle_writes_chunks_manifest_and_protocol(tmp_path):
    week = _write_conversations(tmp_path / "last_week", [f"w{i}" for i in range(25)])
    day = _write_conversations(tmp_path / "last_day", ["w0", "w1", "w2"])
    out = tmp_path / "support-analysis"

    manifest = prepare_agents_bundle(week, day, out, workers=5)

    work = out / "agents_work"
    assert (work / "taxonomy.json").is_file()
    assert (work / "manifest.json").is_file()
    assert (work / "agents_protocol.md").is_file()
    taxonomy = json.loads((work / "taxonomy.json").read_text(encoding="utf-8"))["categories"]
    assert [item["name"] for item in taxonomy] == [item["name"] for item in TAXONOMY]
    assert len(taxonomy) == 10
    chunk_files = sorted(work.glob("chunk_*.json"))
    assert len(chunk_files) == 5
    total = sum(len(json.loads(f.read_text(encoding="utf-8"))["conversations"]) for f in chunk_files)
    assert total == 25  # объединение week ∪ day без дублей
    assert manifest["workers"] == 5
    assert manifest["unique_conversation_count"] == 25
    assert manifest["taxonomy_source"] == "static_v5"
    assert len(manifest["chunks"]) == 5
    for entry in manifest["chunks"]:
        assert Path(entry["result_path"]).name.startswith("result_")
        assert entry["conversation_count"] > 0


def _fill_results(manifest, category="Проблемы подключения VPN", skip_worker=None):
    for entry in manifest["chunks"]:
        if entry["worker"] == skip_worker:
            continue
        chunk = json.loads(Path(entry["chunk_path"]).read_text(encoding="utf-8"))
        items = [{
            "id": conv["id"], "category": "Проблемы подключения VPN" if category is None else category,
            "subcategory": "Не подключается", "problem_summary": f"Суть {conv['id']}",
            "severity": None, "severity_reason": "влияние не описано",
            "confidence": "high", "evidence": "не работает VPN",
        } for conv in chunk["conversations"]]
        Path(entry["result_path"]).write_text(json.dumps({"classifications": items}, ensure_ascii=False), encoding="utf-8")


def test_merge_agents_results_validates_and_builds_report(tmp_path):
    week = _write_conversations(tmp_path / "last_week", [f"w{i}" for i in range(6)])
    day = _write_conversations(tmp_path / "last_day", ["w0", "w1"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=3)
    _fill_results(manifest, category=None)

    outcome = merge_agents_results(week, day, out)

    assert outcome["unique_conversation_count"] == 6
    result = json.loads(Path(outcome["result_path"]).read_text(encoding="utf-8"))
    assert result["complete"] is True
    assert len(result["classifications"]) == 6
    assert [c["name"] for c in result["categories"]] == [c["name"] for c in TAXONOMY]
    assert Path(outcome["report_path"]).is_file()
    assert Path(outcome["dashboard_path"]).is_file()


def test_merge_agents_results_reports_missing_worker(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_results(manifest, category=None, skip_worker=2)

    with pytest.raises(AnalysisError, match="воркер 2"):
        merge_agents_results(week, day, out)


def test_merge_agents_results_rejects_unknown_category(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_results(manifest, category="Выдуманная категория")

    with pytest.raises(AnalysisError, match="не из таксономии"):
        merge_agents_results(week, day, out)


def test_merge_agents_results_compare_with_previous(tmp_path):
    week = _write_conversations(tmp_path / "last_week", ["a", "b"])
    day = _write_conversations(tmp_path / "last_day", ["a"])
    out = tmp_path / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_results(manifest, category=None)
    previous = {"classifications": [
        {"id": "a", "category": "Проблемы подключения VPN", "subcategory": "Не подключается", "severity": None},
        {"id": "b", "category": "Ключи и конфигурации", "subcategory": "s", "severity": None},
    ]}
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")

    outcome = merge_agents_results(week, day, out, compare_with=previous_path)

    comparison = Path(outcome["comparison_path"]).read_text(encoding="utf-8")
    assert "50.0%" in comparison  # категория совпала у 1 из 2
    assert outcome["comparison_path"].endswith("comparison_report.md")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agents_orchestration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'intercom_exporter.agents_orchestration'`

- [ ] **Step 3: Write the implementation**

Создать `src/intercom_exporter/agents_protocol.md`:

```markdown
# Протокол исполнителя классификации

Ты — один из {workers} параллельных исполнителей классификации обращений поддержки.

1. Твой вход — только файл чанка `{chunk_path}`. Чужие чанки не читай.
2. Политика классификации — в `{prompt_path}`: фиксированная таксономия из 10 категорий, правила границ, тяжесть, уверенность, evidence. Соблюдай её полностью.
3. Категории — строго из `{taxonomy_path}`; новые названия не выдумывай, опечатки недопустимы.
4. Работай внутренними батчами по 15–20 обращений; после всех батчей сделай второй проход по записям с confidence=low.
5. Результат — ТОЛЬКО файл `{result_path}` формата {"classifications": [...]}: ровно одна запись на каждый id чанка с полями id, category, subcategory, problem_summary, severity (low|medium|high|null), severity_reason, confidence (low|medium|high), evidence (цитата ≤180 символов или ""). Никаких дайджестов, сводок и аналитики в ответе.
6. Перед завершением программно проверь: JSON валиден, число записей равно числу обращений чанка, каждый id встречается один раз.
```

Создать `src/intercom_exporter/agents_orchestration.py`:

```python
"""Пакет оркестрации субагентов и слияние их результатов.

Оркестрация — поведение рантайма агента: Python детерминированно готовит чанки,
статическую таксономию и манифест, агент-оркестратор по манифесту поднимает
субагентов, а merge_agents_results валидирует и сливает их результаты в формат
chatgpt_result.json и через generate_monitoring_report строит отчёт и дашборд.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from .support_analysis import (
    AnalysisError,
    _build_payload,
    _read_conversations,
    _union_conversations,
    _write_json,
    _write_text,
    generate_monitoring_report,
)

DEFAULT_WORKERS = 10

# Фиксированная таксономия v5 — синхронизирована с разделом 3 шаблона
# support_analysis_prompt_template.md. Менять только вместе с шаблоном.
TAXONOMY = [
    {"name": "Проблемы подключения VPN",
     "description": "VPN не подключается, соединение не устанавливается, обрывается или постоянно переподключается."},
    {"name": "Доступ к сайтам и сервисам",
     "description": "VPN подключён, но не открываются сайты, банки, Госуслуги, мобильные приложения, телефония и другие ресурсы."},
    {"name": "Скорость и качество соединения",
     "description": "Низкая скорость, задержки, зависания, нестабильная работа и проблемы с качеством передачи данных."},
    {"name": "Ключи и конфигурации",
     "description": "Ключ не пришёл, не скачивается, не импортируется, не работает; требуется повторная выдача, замена ключа или сервера."},
    {"name": "Оплата и управление подпиской",
     "description": "Оплата не проходит или не распознаётся, неверное списание, возврат денег, отмена, продление и изменение подписки."},
    {"name": "Установка и настройка",
     "description": "Установка и обновление OpenVPN, настройка приложения или расширения, ошибки запуска, конфликты программ."},
    {"name": "Совместимость и ограничения",
     "description": "Поддержка устройств, операционных систем, роутеров, протоколов, стран, IP-адресов и особенностей подключения."},
    {"name": "Авторизация и доступ к аккаунту",
     "description": "Не приходит код подтверждения, ошибка входа, проблемы с авторизацией через браузерное расширение или личный кабинет."},
    {"name": "Тарифы и вопросы о сервисе",
     "description": "Стоимость, выбор тарифа, условия использования, возможности сервиса, организационные вопросы и коммуникация с поддержкой."},
    {"name": "Прочие и неклассифицируемые обращения",
     "description": "Автоматические уведомления, спам, предложения о сотрудничестве, сообщения не по теме и обращения, в которых недостаточно информации."},
]


def prepare_agents_bundle(
    week_input_path: str | Path,
    day_input_path: str | Path,
    output_dir: str | Path,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, Any]:
    """Режет объединение week+day на чанки и готовит манифест оркестрации."""
    if not 2 <= workers <= 20:
        raise AnalysisError("workers должен быть от 2 до 20")
    output = Path(output_dir)
    week_rows = _read_conversations(week_input_path, allow_empty=True)
    day_rows = _read_conversations(day_input_path, allow_empty=True)
    union_rows = _union_conversations(week_rows, day_rows)
    if not union_rows:
        raise AnalysisError("Недельный и дневной срезы не содержат обращений")
    conversations = _build_payload(union_rows)["conversations"]

    work = output / "agents_work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    _write_json(work / "taxonomy.json", {"categories": TAXONOMY})

    chunks = [conversations[i::workers] for i in range(workers)]
    chunk_entries = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_path = work / f"chunk_{index:02d}.json"
        _write_json(chunk_path, {"conversations": chunk})
        chunk_entries.append({
            "worker": index,
            "chunk_path": str(chunk_path),
            "result_path": str(work / f"result_{index:02d}.json"),
            "conversation_count": len(chunk),
        })

    protocol_template = Path(__file__).with_name("agents_protocol.md").read_text(encoding="utf-8")
    manifest = {
        "workers": workers,
        "unique_conversation_count": len(conversations),
        "taxonomy_source": "static_v5",
        "taxonomy_path": str(work / "taxonomy.json"),
        "prompt_path": str(output / "PROMPT.md"),
        "work_dir": str(work),
        "chunks": chunk_entries,
    }
    _write_json(work / "manifest.json", manifest)
    _write_text(work / "agents_protocol.md", protocol_template)
    return manifest


def _normalized_classification(item: dict) -> dict:
    return {
        "id": str(item["id"]),
        "category": item["category"],
        "subcategory": str(item.get("subcategory") or "—").strip() or "—",
        "problem_summary": str(item.get("problem_summary") or "—").strip() or "—",
        "severity": item["severity"] if item.get("severity") in ("low", "medium", "high") else None,
        "severity_reason": str(item.get("severity_reason") or "—").strip() or "—",
        "confidence": item["confidence"],
        "evidence": str(item.get("evidence") or ""),
    }


def merge_agents_results(
    week_input_path: str | Path,
    day_input_path: str | Path,
    output_dir: str | Path,
    compare_with: str | Path | None = None,
) -> dict[str, Any]:
    """Валидирует result_XX.json субагентов, собирает chatgpt_result.json и строит отчёт+дашборд."""
    output = Path(output_dir)
    work = output / "agents_work"
    manifest_path = work / "manifest.json"
    if not manifest_path.is_file():
        raise AnalysisError(f"Манифест оркестрации не найден: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))

    week_rows = _read_conversations(week_input_path, allow_empty=True)
    day_rows = _read_conversations(day_input_path, allow_empty=True)
    union_rows = _union_conversations(week_rows, day_rows)
    union_payload = _build_payload(union_rows)
    union_ids = [item["id"] for item in union_payload["conversations"]]
    category_names = {item["name"] for item in TAXONOMY}

    merged: dict[str, dict] = {}
    errors: list[str] = []
    for entry in manifest["chunks"]:
        result_path = Path(entry["result_path"])
        if not result_path.is_file():
            errors.append(f"воркер {entry['worker']}: нет файла {result_path}")
            continue
        try:
            data = json.loads(result_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"воркер {entry['worker']}: невалидный JSON в {result_path}")
            continue
        for item in data.get("classifications", []):
            conversation_id = str(item.get("id"))
            if conversation_id in merged:
                errors.append(f"дубликат ID {conversation_id} (воркер {entry['worker']})")
            if item.get("category") not in category_names:
                errors.append(f"{conversation_id}: категория не из таксономии: {item.get('category')!r}")
            if item.get("confidence") not in ("low", "medium", "high"):
                errors.append(f"{conversation_id}: неверное confidence: {item.get('confidence')!r}")
            merged[conversation_id] = item
    missing = [cid for cid in union_ids if cid not in merged]
    if missing:
        errors.append(f"не покрыты ID ({len(missing)}): {', '.join(missing[:10])}")
    if errors:
        raise AnalysisError("Ошибки слияния результатов субагентов:\n- " + "\n- ".join(errors))

    classifications = [_normalized_classification(merged[cid]) for cid in union_ids]
    week_ids = {item["id"] for item in _build_payload(week_rows)["conversations"]}
    day_ids = {item["id"] for item in _build_payload(day_rows)["conversations"]}

    def summary(label: str, ids: set[str]) -> str:
        subset = [item for item in classifications if item["id"] in ids]
        cats = Counter(item["category"] for item in subset)
        sev = Counter(item["severity"] or "unknown" for item in subset)
        top = ", ".join(f"{name} — {count}" for name, count in cats.most_common(3))
        return (f"{label}: {len(subset)} обращений. Топ категорий: {top}. "
                f"Тяжесть: high {sev.get('high', 0)}, medium {sev.get('medium', 0)}, "
                f"low {sev.get('low', 0)}, не оценена {sev.get('unknown', 0)}.")

    result = {
        "complete": True,
        "conversation_count": len(classifications),
        "dataset_fingerprint": union_payload["dataset_fingerprint"],
        "categories": TAXONOMY,
        "classifications": classifications,
        "executive_summary": summary("Объединение", set(union_ids)),
        "recommendations": ["Сформируйте рекомендации по отчёту report.md."],
        "period_analysis": {
            "week": {
                "conversation_count": len(week_rows),
                "dataset_fingerprint": _build_payload(week_rows)["dataset_fingerprint"],
                "executive_summary": summary("Неделя", week_ids),
                "recommendations": ["См. рекомендации объединения."],
            },
            "day": {
                "conversation_count": len(day_rows),
                "dataset_fingerprint": _build_payload(day_rows)["dataset_fingerprint"],
                "executive_summary": summary("День", day_ids),
                "recommendations": ["См. рекомендации объединения."],
            },
        },
    }
    result_path = output / "chatgpt_result.json"
    _write_json(result_path, result)

    report = generate_monitoring_report(week_input_path, day_input_path, result_path, output)
    outcome: dict[str, Any] = {
        "unique_conversation_count": len(classifications),
        "result_path": str(result_path),
        "report_path": report["report_path"],
        "classifications_path": report["classifications_path"],
        "dashboard_path": report["dashboard_path"],
    }
    if compare_with:
        outcome["comparison_path"] = str(_write_comparison(compare_with, result, output))
    return outcome


def _write_comparison(reference_path: str | Path, result: dict, output: Path) -> Path:
    """Метрики совпадения и список расхождений с предыдущей разметкой."""
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8-sig"))
    ref_by_id = {item["id"]: item for item in reference["classifications"]}
    new_by_id = {item["id"]: item for item in result["classifications"]}
    common = [cid for cid in new_by_id if cid in ref_by_id]
    if not common:
        raise AnalysisError("В сравниваемых разметках нет общих ID")
    cat_match = sub_match = sev_match = full = 0
    confusion: Counter = Counter()
    diffs = []
    for cid in common:
        old, new = ref_by_id[cid], new_by_id[cid]
        cm = old["category"] == new["category"]
        sm = old["subcategory"] == new["subcategory"]
        vm = (old["severity"] or "unknown") == (new["severity"] or "unknown")
        cat_match += cm
        sub_match += sm
        sev_match += vm
        full += cm and sm and vm
        if not cm:
            confusion[(old["category"], new["category"])] += 1
        if not (cm and sm and vm):
            diffs.append((cid, old["category"], new["category"], old["subcategory"], new["subcategory"],
                          old["severity"] or "—", new["severity"] or "—"))
    total = len(common)
    lines = [
        "# Сравнение разметки с предыдущим прогоном", "",
        f"Общих ID: **{total}**.", "",
        "| Поле | Совпадений | Доля |", "|---|---:|---:|",
        f"| Категория | {cat_match} | {cat_match / total * 100:.1f}% |",
        f"| Подкатегория | {sub_match} | {sub_match / total * 100:.1f}% |",
        f"| Тяжесть | {sev_match} | {sev_match / total * 100:.1f}% |",
        f"| Полное совпадение | {full} | {full / total * 100:.1f}% |",
        "", "## Матрица расхождений категорий (топ-20)", "",
        "| Было | Стало | Сколько |", "|---|---|---:|",
    ]
    for (before, after), count in confusion.most_common(20):
        lines.append(f"| {before} | {after} | {count} |")
    lines += ["", "## Список расхождений", "",
              "| ID | Категория была | Категория стала | Подкат. была | Подкат. стала | Тяжесть была | Тяжесть стала |",
              "|---|---|---|---|---|---|---|"]
    for row in diffs:
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |")
    comparison_path = output / "comparison_report.md"
    _write_text(comparison_path, "\n".join(lines))
    return comparison_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agents_orchestration.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/agents_orchestration.py src/intercom_exporter/agents_protocol.md tests/test_agents_orchestration.py
git commit -m "feat: agents orchestration bundle, merge and comparison (static taxonomy v5)"
```

---

### Task 3: CLI — оркестрация по умолчанию

**Files:**
- Modify: `src/intercom_exporter/support_analysis.py` (`build_parser`, `main`)
- Test: `tests/test_agents_orchestration.py`

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_agents_orchestration.py`:

```python
def test_cli_prepare_monitoring_creates_agents_bundle_by_default(tmp_path, capsys):
    from intercom_exporter.support_analysis import main

    week = _write_conversations(tmp_path / "run" / "last_week", ["a", "b", "c"])
    day = _write_conversations(tmp_path / "run" / "last_day", ["a"])
    code = main(["prepare-monitoring", "--week", str(week), "--day", str(day)])
    assert code == 0
    bundle = tmp_path / "run" / "support-analysis" / "agents_work"
    assert (bundle / "manifest.json").is_file()
    assert (bundle / "agents_protocol.md").is_file()
    assert "оркестрац" in capsys.readouterr().out.lower()


def test_cli_prepare_monitoring_agents_disabled_with_flag(tmp_path):
    from intercom_exporter.support_analysis import main

    week = _write_conversations(tmp_path / "run" / "last_week", ["a"])
    day = _write_conversations(tmp_path / "run" / "last_day", ["a"])
    code = main(["prepare-monitoring", "--week", str(week), "--day", str(day), "--no-agents"])
    assert code == 0
    assert not (tmp_path / "run" / "support-analysis" / "agents_work").exists()


def test_cli_merge_agents(tmp_path, capsys):
    from intercom_exporter.support_analysis import main

    week = _write_conversations(tmp_path / "run" / "last_week", ["a"])
    day = _write_conversations(tmp_path / "run" / "last_day", ["a"])
    out = tmp_path / "run" / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_results(manifest, category=None)

    code = main(["merge-agents", "--week", str(week), "--day", str(day)])
    assert code == 0
    assert "Дашборд" in capsys.readouterr().out
    assert (out / "dashboard.html").is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agents_orchestration.py -v -k cli`
Expected: FAIL — unrecognized arguments: --no-agents / invalid choice: 'merge-agents'

- [ ] **Step 3: Implementation**

В `src/intercom_exporter/support_analysis.py`:

1. `build_parser()`: у `monitor_prepare` добавить:

```python
    monitor_prepare.add_argument("--workers", type=int, default=10,
                                 help="Число параллельных субагентов для пакета оркестрации (2–20, по умолчанию 10)")
    monitor_prepare.add_argument("--no-agents", action="store_true",
                                 help="Не создавать пакет оркестрации субагентов (только PROMPT.md и входные JSON)")
```

и новый subcommand после блока `monitor_report`:

```python
    merge = subparsers.add_parser("merge-agents", help="Слить результаты субагентов, проверить и построить отчёт с дашбордом")
    merge.add_argument("--week", required=True, help="Исходный conversations.json недельного окна")
    merge.add_argument("--day", required=True, help="Исходный conversations.json дневного окна")
    merge.add_argument("--output", help="Каталог анализа (по умолчанию: support-analysis рядом с данными)")
    merge.add_argument("--compare-with", help="Прошлый chatgpt_result.json для отчёта сравнения")
```

2. `main()`, ветка `prepare-monitoring`: сразу после `result = prepare_monitoring_analysis(...)` и существующего print вставить:

```python
            if not args.no_agents:
                from .agents_orchestration import prepare_agents_bundle

                bundle = prepare_agents_bundle(args.week, args.day, result["output_dir"], workers=args.workers)
                print(
                    f"Пакет оркестрации субагентов готов: {bundle['workers']} чанков, "
                    f"таксономия: {bundle['taxonomy_source']}. Манифест: {bundle['work_dir']}/manifest.json. "
                    "Запустите субагентов по протоколу agents_protocol.md, затем: merge-agents --week ... --day ..."
                )
```

3. `main()`: новая ветка `elif args.command == "merge-agents":` ПЕРЕД финальным `else` (который обслуживает `report-monitoring`):

```python
        elif args.command == "merge-agents":
            from .agents_orchestration import merge_agents_results

            week_path, day_path = Path(args.week), Path(args.day)
            if args.output:
                output = Path(args.output)
            elif week_path.parent.name == "last_week" and day_path.parent.name == "last_day" and week_path.parent.parent == day_path.parent.parent:
                output = week_path.parent.parent / "support-analysis"
            else:
                output = week_path.parent / "support-analysis"
            result = merge_agents_results(week_path, day_path, output, compare_with=args.compare_with)
            print(
                f"Слито классификаций: {result['unique_conversation_count']}. Результат: {result['result_path']}. "
                f"Отчёт: {result['report_path']}. Дашборд: {result['dashboard_path']}"
            )
            if "comparison_path" in result:
                print(f"Сравнение с предыдущим прогоном: {result['comparison_path']}")
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agents_orchestration.py -v` — expected 8 passed
Run: `.venv/Scripts/python.exe -m pytest tests/ -q` — весь набор зелёный, кроме предсуществующего `test_prepare_analyze_dahl_uses_dahl_settings`.

- [ ] **Step 5: Commit**

```bash
git add src/intercom_exporter/support_analysis.py tests/test_agents_orchestration.py
git commit -m "feat: agents orchestration is the default mode (prepare-monitoring + merge-agents)"
```

---

### Task 4: Документация и smoke-проверка

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README** — в раздел мониторинга добавить:

```markdown
Анализ использует фиксированную таксономию из 10 категорий с правилами границ (см. `support_analysis_prompt_template.md`, раздел 3).

По умолчанию `prepare-monitoring` дополнительно готовит пакет оркестрации субагентов (`agents_work/`: чанки, `taxonomy.json`, `manifest.json`, `agents_protocol.md`). Дальше агент-оркестратор поднимает субагентов по манифесту (каждый классифицирует свой чанк по `agents_protocol.md`), после чего:

```bash
python analyze_support.py merge-agents --week .../last_week/conversations.json --day .../last_day/conversations.json [--compare-with предыдущий/chatgpt_result.json]
```

`merge-agents` валидирует результаты (покрытие ID, дубликаты, категории из таксономии), собирает `chatgpt_result.json`, строит `report.md` и `dashboard.html`, а с `--compare-with` — `comparison_report.md`. Отключить пакет: `--no-agents`. Число субагентов: `--workers N` (по умолчанию 10). Режимы `--analyze openrouter|dahl` остаются альтернативой.
```

- [ ] **Step 2: Smoke-проверка**

```bash
.venv/Scripts/python.exe analyze_support.py prepare-monitoring --week "data/20261026T134214Z_20261102T134214Z/last_week/conversations.json" --day "data/20261026T134214Z_20261102T134214Z/last_day/conversations.json"
```

Expected: exit 0; в выводе строка про пакет оркестрации; `agents_work/manifest.json` существует с `taxonomy_source = "static_v5"`; эталонные `chatgpt_result.json` и `dashboard_agents.html` не тронуты (пакет пишется только в `agents_work/`).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: fixed taxonomy v5 and agents orchestration default workflow"
```

---

## Self-Review

- **Spec coverage:** таксономия из 10 категорий + 6 правил границ — Task 1 (verbatim в шаблон); статически в промпт — Task 1 + TAXONOMY в Task 2 (один источник, синхронизация задокументирована); чистый переход — старая разметка не мигрируется, зафиксировано в Architecture; KPI под категорию 5 — Task 1 Step 4; свободные подкатегории — раздел 3 шаблона; оркестрация по умолчанию — Task 3; «сами разбивают на батчи» — протокол п.4; «потом сработал скрипт дашборда» — merge вызывает generate_monitoring_report; сравнение — --compare-with.
- **Placeholders:** полный код во всех шагах.
- **Type consistency:** `prepare_agents_bundle(week, day, output_dir, workers=10) -> manifest{workers, unique_conversation_count, taxonomy_source, taxonomy_path, prompt_path, work_dir, chunks[{worker, chunk_path, result_path, conversation_count}]}`; `merge_agents_results(week, day, output_dir, compare_with=None) -> {unique_conversation_count, result_path, report_path, classifications_path, dashboard_path[, comparison_path]}`; `billing_week` согласован между dashboard.py, _TEMPLATE и тестами; хелпер `_fill_results` в тестах используется во всех merge-тестах Task 2–3 с одинаковой сигнатурой.

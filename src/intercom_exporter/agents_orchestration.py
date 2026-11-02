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
    _monitoring_prompt,
    _period_label,
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

# KPI дашборда: категория «Оплата и управление подпиской». Не зависит от индекса в TAXONOMY.
BILLING_CATEGORY = "Оплата и управление подпиской"
assert any(item["name"] == BILLING_CATEGORY for item in TAXONOMY), "BILLING_CATEGORY должна быть в TAXONOMY"


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
    workers = min(workers, len(conversations))

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

    prompt_file = output / "PROMPT.md"
    if not prompt_file.is_file():
        week_payload = _build_payload(week_rows)
        day_payload = _build_payload(day_rows)
        week_payload["period"] = _period_label(Path(week_input_path)) or "Последние 7×24 часа UTC"
        day_payload["period"] = _period_label(Path(day_input_path)) or "Последние 24 часа UTC"
        combined_payload = _build_payload(union_rows)
        week_file, day_file = output / "chatgpt_input_week.json", output / "chatgpt_input_day.json"
        week_bytes = week_file.stat().st_size if week_file.is_file() else 0
        day_bytes = day_file.stat().st_size if day_file.is_file() else 0
        _write_text(prompt_file, _monitoring_prompt(
            week_payload, day_payload, combined_payload, week_bytes, day_bytes,
        ))

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
    union_id_set = set(union_ids)
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
        if not isinstance(data.get("classifications"), list):
            errors.append(f"воркер {entry['worker']}: classifications не является списком в {result_path}")
            continue
        for item in data["classifications"]:
            if not isinstance(item, dict) or item.get("id") is None:
                errors.append(f"воркер {entry['worker']}: некорректная запись классификации")
                continue
            conversation_id = str(item.get("id"))
            if conversation_id in merged:
                errors.append(f"дубликат ID {conversation_id} (воркер {entry['worker']})")
            if conversation_id not in union_id_set:
                errors.append(f"неизвестный ID {conversation_id} (воркер {entry['worker']})")
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
        old_cat, new_cat = old.get("category") or "—", new.get("category") or "—"
        old_sub, new_sub = old.get("subcategory") or "—", new.get("subcategory") or "—"
        old_sev, new_sev = old.get("severity") or "unknown", new.get("severity") or "unknown"
        cm = old_cat == new_cat
        sm = old_sub == new_sub
        vm = old_sev == new_sev
        cat_match += cm
        sub_match += sm
        sev_match += vm
        full += cm and sm and vm
        if not cm:
            confusion[(old_cat, new_cat)] += 1
        if not (cm and sm and vm):
            diffs.append((cid, old_cat, new_cat, old_sub, new_sub,
                          old.get("severity") or "—", new.get("severity") or "—"))
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
        before_esc = str(before).replace("|", "\\|")
        after_esc = str(after).replace("|", "\\|")
        lines.append(f"| {before_esc} | {after_esc} | {count} |")
    lines += ["", "## Список расхождений", "",
              "| ID | Категория была | Категория стала | Подкат. была | Подкат. стала | Тяжесть была | Тяжесть стала |",
              "|---|---|---|---|---|---|---|"]
    for row in diffs:
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |")
    comparison_path = output / "comparison_report.md"
    _write_text(comparison_path, "\n".join(lines))
    return comparison_path

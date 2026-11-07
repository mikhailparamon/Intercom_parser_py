"""Пакет оркестрации субагентов и слияние их результатов.

Оркестрация — поведение рантайма агента: Python детерминированно готовит чанки,
статическую таксономию и манифест, агент-оркестратор по манифесту поднимает
субагентов, а merge_agents_results валидирует и сливает их результаты в формат
chatgpt_result.json и через generate_monitoring_report строит отчёт и дашборд.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from .classification_catalog import SCHEMA_VERSION, canonical_classification, confirmed_platforms, get_subcategories

from .other_review import review_other_classifications
from .support_analysis import (
    AnalysisError,
    _build_payload,
    _monitoring_prompt,
    _normalize_problem_subcategory,
    _normalize_platforms,
    _docs_tariff_operator_evidence,
    DOCS_TARIFF_SUBCATEGORY,
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
    {"name": "Оплата и подписка",
     "description": "Оплата не проходит или не распознаётся, неверное списание, возврат денег, отмена, продление и изменение подписки."},
    {"name": "Установка и настройка",
     "description": "Установка и обновление OpenVPN, настройка приложения или расширения, ошибки запуска, конфликты программ."},
    {"name": "Совместимость и ограничения",
     "description": "Поддержка устройств, операционных систем, роутеров, протоколов, стран, IP-адресов и особенностей подключения."},
    {"name": "Авторизация и аккаунт",
     "description": "Не приходит код подтверждения, ошибка входа, проблемы с авторизацией через браузерное расширение или личный кабинет."},
    {"name": "Тарифы и вопросы о сервисе",
     "description": "Стоимость, выбор тарифа, условия использования, возможности сервиса, организационные вопросы и коммуникация с поддержкой."},
    {"name": "Прочие обращения",
     "description": "Нет запроса, автоматическое сообщение, спам, не по теме сервиса, вложение без описания или неустановимый предмет запроса после проверки всей переписки и заголовка. Неизвестная причина сбоя не основание для этой категории."},
]

# KPI дашборда: категория «Оплата и подписка». Не зависит от индекса в TAXONOMY.
BILLING_CATEGORY = "Оплата и подписка"
assert any(item["name"] == BILLING_CATEGORY for item in TAXONOMY), "BILLING_CATEGORY должна быть в TAXONOMY"

# Подкатегории свободные и описывают проблему или запрос, а не платформу.
FIXED_SUBCATEGORIES: list[str] = []

# Фиксированная таксономия HyperTube v2 — синхронизирована с разделом 3 шаблона
# support_analysis_prompt_template_hypertube.md. Менять только вместе с шаблоном.
HYPERTUBE_TAXONOMY = [
    {"name": "Расширение браузера",
     "description": "Проблемы с браузерным расширением HyperTube: не работает в Chrome/Yandex/Opera/Edge, не устанавливается, иконка неактивна, чистка кэша браузера."},
    {"name": "Доступ к YouTube",
     "description": "YouTube не открывается, пишет «нет интернета», сайт недоступен, видео не загружаются; пользователь не указывает приложение/расширение или проблема явно на стороне доступа к YouTube."},
    {"name": "Оплата и подписка",
     "description": "Вопросы оплаты Premium, продления, пробного периода, активации подписки после оплаты, возврата средств."},
    {"name": "Установка и настройка",
     "description": "Установка, переустановка, обновление расширения или приложения; первичная настройка, выбор сервера, включение/выключение."},
    {"name": "Приложение",
     "description": "Проблемы с мобильным или TV приложением HyperTube: не запускается, не подключается, вылетает, кэш, обновление приложения."},
    {"name": "Прочие обращения",
     "description": "Нет запроса, автоматическое сообщение, спам, не по теме сервиса, вложение без описания или неустановимый предмет запроса после проверки всей переписки и заголовка. Неизвестная причина сбоя не основание для этой категории."},
    {"name": "Скорость и качество видео",
     "description": "YouTube тормозит, лагает, медленная загрузка, низкое разрешение, постоянная буферизация."},
    {"name": "Авторизация и аккаунт",
     "description": "Код входа не приходит или истекает, вход в аккаунт, привязка устройства, сброс настроек после переустановки системы."},
    {"name": "Совместимость и ограничения",
     "description": "Поддержка устройств, операционных систем, браузеров, стран, регионов, телевизоров, Smart TV и особенностей подключения."},
    {"name": "Тарифы и вопросы о сервисе",
     "description": "Стоимость, выбор тарифа, условия использования, возможности сервиса, организационные вопросы и коммуникация с поддержкой."},
]
HYPERTUBE_BILLING_CATEGORY = "Оплата и подписка"

HYPERTUBE_FIXED_SUBCATEGORIES: list[str] = []


def get_taxonomy(service_id: str | None = None) -> list[dict]:
    """Таксономия категорий для сервиса."""
    if service_id == "hypertube":
        return list(HYPERTUBE_TAXONOMY)
    return list(TAXONOMY)


def get_billing_category(service_id: str | None = None) -> str:
    if service_id == "hypertube":
        return HYPERTUBE_BILLING_CATEGORY
    return BILLING_CATEGORY


def get_fixed_subcategories(service_id: str | None = None) -> list[str]:
    """Совместимость со старым API: платформенные подкатегории больше не фиксируются."""
    if service_id == "hypertube":
        return list(HYPERTUBE_FIXED_SUBCATEGORIES)
    return list(FIXED_SUBCATEGORIES)


def prepare_agents_bundle(
    week_input_path: str | Path,
    day_input_path: str | Path,
    output_dir: str | Path,
    workers: int = DEFAULT_WORKERS,
    taxonomy: list[dict] | None = None,
    taxonomy_source: str = "static_v5",
    service_id: str | None = None,
    schema_version: int = SCHEMA_VERSION,
) -> dict[str, Any]:
    """Режет объединение week+day на чанки и готовит манифест оркестрации.

    Пустая таксономия считается признаком «ещё не утверждена» и приводит к ошибке,
    чтобы случайно не запустить классификацию без категорий.
    """
    if taxonomy is None:
        taxonomy = get_taxonomy(service_id)
    if not taxonomy:
        raise AnalysisError("Таксономия сервиса не задана. Сначала утвердите список категорий.")
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

    _write_json(work / "taxonomy.json", {
        "categories": taxonomy, "subcategories": get_subcategories(service_id or "vpn"),
        "classification_schema_version": schema_version, "service_id": service_id or "vpn",
    })

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
            service_id=service_id,
        ))

    protocol_template = Path(__file__).with_name("agents_protocol.md").read_text(encoding="utf-8")
    manifest = {
        "classification_schema_version": schema_version,
        "service_id": service_id or "vpn",
        "workers": workers,
        "unique_conversation_count": len(conversations),
        "taxonomy_source": taxonomy_source,
        "taxonomy_path": str(work / "taxonomy.json"),
        "prompt_path": str(output / "PROMPT.md"),
        "work_dir": str(work),
        "chunks": chunk_entries,
    }
    _write_json(work / "manifest.json", manifest)
    _write_text(work / "agents_protocol.md", protocol_template)
    return manifest


def _normalized_classification(item: dict) -> dict:
    platforms = item.get("platforms", [])
    if not isinstance(platforms, list):
        platforms = []
    platforms = list(dict.fromkeys(
        value.strip() for value in platforms
        if isinstance(value, str) and value.strip()
    ))
    platforms = _normalize_platforms(platforms)
    additional_topics = item.get("additional_topics", [])
    if not isinstance(additional_topics, list):
        additional_topics = []
    additional_topics = list(dict.fromkeys(
        value.strip() for value in additional_topics
        if isinstance(value, str) and value.strip()
    ))
    return {
        **({"subcategory_code": item["subcategory_code"]} if "subcategory_code" in item else {}),
        "id": str(item["id"]),
        "category": item["category"],
        "subcategory": str(item.get("subcategory") or "—").strip() or "—",
        "problem_summary": str(item.get("problem_summary") or "—").strip() or "—",
        "severity": item["severity"] if item.get("severity") in ("low", "medium", "high") else None,
        "severity_reason": str(item.get("severity_reason") or "—").strip() or "—",
        "confidence": item["confidence"],
        "evidence": str(item.get("evidence") or ""),
        "platforms": platforms,
        "additional_topics": additional_topics,
    }


def _conversation_text(conversation: dict | None, roles: tuple[str, ...] = ("customer",)) -> str:
    """Возвращает склеенный текст сообщений разговора, фильтруя по ролям."""
    if not conversation:
        return ""
    parts = [conversation.get("title", "")]
    for message in conversation.get("messages", []):
        if message.get("role") in roles:
            parts.append(message.get("text", ""))
    return " ".join(part for part in parts if part)


def _normalize_subcategory(value: str) -> str:
    """Нормализует очевидные варианты подкатегорий без замены их на платформу."""
    return _normalize_problem_subcategory(value)


def _apply_fixed_subcategory_rules(subcategory: str, conversation: dict | None) -> str:
    """Сохраняет проблему из классификации; платформа берётся отдельным полем."""
    return _normalize_subcategory(subcategory)


def _finalize_classification(item: dict, conversation: dict | None) -> dict:
    normalized = _normalized_classification(item)
    if "subcategory_code" in item:
        normalized = canonical_classification(normalized, "vpn")
        normalized["platforms"] = confirmed_platforms(conversation or {}, "vpn")
    normalized["subcategory"] = _apply_fixed_subcategory_rules(normalized["subcategory"], conversation)
    if (
        normalized["subcategory"] != DOCS_TARIFF_SUBCATEGORY
        and DOCS_TARIFF_SUBCATEGORY not in normalized["additional_topics"]
        and _docs_tariff_operator_evidence(conversation)
    ):
        normalized["additional_topics"].append(DOCS_TARIFF_SUBCATEGORY)
    return normalized


def _normalize_hypertube_subcategory(value: str) -> str:
    """Сохраняет смысловую подкатегорию HyperTube, нормализуя общие синонимы."""
    return _normalize_subcategory(value)


def _apply_hypertube_fixed_subcategory_rules(subcategory: str, conversation: dict | None) -> str:
    """Не выводит платформу из текста вместо содержательной подкатегории."""
    return _normalize_hypertube_subcategory(subcategory)


def _refine_hypertube_unspecified_subcategory(
    subcategory: str, category: str, conversation: dict | None,
) -> str:
    """Исправляет только явно распознаваемые симптомы HyperTube; не угадывает причину."""
    if subcategory.strip().casefold() not in {
        "проблема не уточнена", "не уточнена", "недостаточно контекста",
    }:
        return subcategory

    text = _conversation_text(conversation).casefold().replace("ё", "е")
    negative = r"(?:не\s*(?:работ\w*|груз\w*|загружа\w*|открыва\w*|подключа\w*|запуска\w*|включа\w*)|перестал\w*\s+(?:работ\w*|открыва\w*)|сбо\w*|не\s+помога\w*)"

    if category == "Доступ к YouTube":
        if re.search(r"ускорени\w*.{0,50}" + negative + r"|" + negative + r".{0,50}ускорени\w*", text):
            return "Ускорение не работает"
        if re.search(
            r"нет\s+(?:(?:подключени\w*|соединени\w*)\s+к\s+)?интернет\w*|"
            r"ошибк\w*\s+подключени\w*\s+к\s+интернет\w*|интернет\w*\s+не\s+работ\w*",
            text,
        ):
            return "Нет подключения к интернету"
        if re.search(r"(?:видео|ролик\w*).{0,45}(?:не\s+(?:груз\w*|загружа\w*|открыва\w*)|буферизац\w*)", text):
            return "Видео не загружается"
        if re.search(r"(?:ютуб|youtube|hypertube|гипертуб).{0,60}" + negative + r"|" + negative + r".{0,60}(?:ютуб|youtube|hypertube|гипертуб)", text):
            return "YouTube не работает"

    if category == "Расширение браузера" and re.search(
        r"расширени\w*.{0,70}" + negative + r"|" + negative + r".{0,70}расширени\w*", text,
    ):
        return "Расширение не работает"
    if category == "Расширение браузера" and re.search(r"плох\w*.{0,20}работ\w*|нестабильн\w*.{0,30}работ\w*", text):
        return "Расширение работает нестабильно"
    if re.search(r"ad\s*block|адблок", text) and re.search(r"блокир\w*|блок\w*|блоч\w*", text):
        if category == "Расширение браузера":
            return "Конфликт с блокировщиком рекламы"

    if category == "Приложение":
        if re.search(r"кнопк\w*.{0,30}(?:нет|отсутств\w*)|(?:нет|отсутств\w*).{0,30}кнопк\w*.{0,30}подключ", text):
            return "Кнопка подключения отсутствует"
        if re.search(r"(?:когда|почему).{0,35}приложени\w*.{0,35}заработ\w*", text):
            return "Приложение не работает"
        if re.search(r"(?:все|всё|приложени\w*).{0,30}(?:висн\w*|завис\w*|тормоз\w*)", text):
            return "Приложение зависает"
        if re.search(r"подозрительн\w* трафик", text):
            return "Ошибка подозрительного трафика"

    if category == "Приложение":
        if re.search(r"приложени\w*.{0,50}(?:завис\w*|висн\w*|тормоз\w*)", text):
            return "Приложение зависает"
        if re.search(r"приложени\w*.{0,70}" + negative + r"|" + negative + r".{0,70}приложени\w*", text):
            return "Приложение не работает"

    if category == "Скорость и качество видео":
        if re.search(r"(?:низк\w* скорост\w*|медленн\w* загруз\w*|тормоз\w*|лаг\w*|буферизац\w*|завис\w*)", text):
            return "Низкая скорость или буферизация"
        if re.search(r"ускорени\w*.{0,50}" + negative + r"|" + negative + r".{0,50}ускорени\w*", text):
            return "Ускорение не работает"

    if category == "Установка и настройка" and re.search(r"(?:vpn|впн).{0,50}" + negative + r"|" + negative + r".{0,50}(?:vpn|впн)", text):
        return "VPN не работает"

    if category == "Оплата и подписка":
        if re.search(r"(?:как|хочу|можно).{0,30}продл\w* подписк\w*|продл\w* подписк\w*", text):
            return "Продление подписки"
        if re.search(r"оплатил\w*.{0,50}(?:ничего не работает|не работает)|премиум.{0,40}(?:не груз\w*|не работает)|(?:не груз\w*|не работает).{0,40}премиум", text):
            return "Проблема после оплаты"

    if category == "Авторизация и аккаунт":
        if re.search(r"код.{0,45}(?:не приш\w*|не получ\w*|не поступ\w*)|(?:не приш\w*|не получ\w*|не поступ\w*).{0,45}код", text):
            return "Код не приходит"
        if re.search(r"не могу войти|не получается войти|ошибк\w* вход\w*", text):
            return "Ошибка входа"
        if re.search(r"код.{0,35}для вход\w*", text) and re.search(negative, text):
            return "Ошибка входа"

    if category == "Установка и настройка" and re.search(r"переустанов\w*", text):
        return "Переустановка приложения или расширения"

    if category == "Доступ к YouTube" and _hypertube_customer_confirmed_device_context(conversation):
        return "YouTube не работает"

    # После чтения переписки категория уже фиксирует объект проблемы.
    # Если клиент прямо сообщает «не работает», это достаточный симптом,
    # даже если подробная техническая причина неизвестна.
    if re.search(negative, text):
        generic_symptoms = {
            "Доступ к YouTube": "YouTube не работает",
            "Расширение браузера": "Расширение не работает",
            "Приложение": "Приложение не работает",
        }
        if category in generic_symptoms:
            return generic_symptoms[category]

    return subcategory


def _hypertube_customer_confirmed_device_context(conversation: dict | None) -> bool:
    """Recognize a device answer to support's YouTube question as confirmed context."""
    if not conversation:
        return False
    messages = conversation.get("messages", [])
    device_question_seen = False
    for message in messages:
        text = str(message.get("text", "")).casefold().replace("ё", "е")
        if message.get("role") == "operator" and re.search(
            r"на каком устройстве.{0,100}(?:не работает|не открывается).{0,30}(?:ютуб|youtube)", text,
        ):
            device_question_seen = True
        elif device_question_seen and message.get("role") == "customer" and re.search(
            r"\b(?:на компьютере|на ноутбуке|на телефоне|на телевизоре|на планшете|на пк)\b", text,
        ):
            return True
    return False


def _finalize_hypertube_classification(item: dict, conversation: dict | None) -> dict:
    normalized = _normalized_classification(item)
    if "subcategory_code" in item:
        normalized = canonical_classification(normalized, "hypertube")
        normalized["platforms"] = confirmed_platforms(conversation or {}, "hypertube")
        return normalized
    normalized["subcategory"] = _apply_hypertube_fixed_subcategory_rules(normalized["subcategory"], conversation)
    normalized["subcategory"] = _refine_hypertube_unspecified_subcategory(
        normalized["subcategory"], normalized["category"], conversation,
    )
    return normalized


def _load_recommendations(recommendations: str | Path | list[str] | None) -> list[str]:
    """Нормализует общие рекомендации: список строк или JSON-файл со списком строк."""
    if recommendations is None:
        return []
    if isinstance(recommendations, (str, Path)):
        path = Path(recommendations)
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AnalysisError(f"Не удалось прочитать рекомендации из {path}: {exc}") from exc
    else:
        value = recommendations
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise AnalysisError("Рекомендации должны быть списком непустых строк")
    return [item.strip() for item in value]


def _default_recommendations(classifications: list[dict]) -> list[str]:
    """Детерминированные рекомендации по статистике классификаций, если оркестратор не передал свои."""
    total = len(classifications)
    counts = Counter(item["category"] for item in classifications)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    recommendations = [
        f"Приоритет разбора — «{name}»: {count} обращений ({count / total * 100:.1f}%). "
        "Проверьте и обновите шаблонные ответы и инструкции по этой категории."
        for name, count in ranked[:3]
    ]
    high = sum(1 for item in classifications if item["severity"] == "high")
    if high:
        recommendations.append(
            f"Обращений высокой критичности: {high}. Разберите их вне очереди — это зона прямого оттока клиентов."
        )
    unrated = sum(1 for item in classifications if item["severity"] is None)
    if unrated / total >= 0.2:
        recommendations.append(
            f"У {unrated} обращений ({unrated / total * 100:.1f}%) критичность не оценена: просите операторов "
            "фиксировать влияние на клиента при закрытии — это улучшит качество отчётов."
        )
    return recommendations


def merge_agents_results(
    week_input_path: str | Path,
    day_input_path: str | Path,
    output_dir: str | Path,
    compare_with: str | Path | None = None,
    recommendations: str | Path | list[str] | None = None,
    dashboard_dir: str | Path | None = None,
    taxonomy: list[dict] | None = None,
    taxonomy_source: str = "static_v5",
    billing_category: str | None = None,
    service_id: str | None = None,
) -> dict[str, Any]:
    """Валидирует result_XX.json субагентов, собирает chatgpt_result.json и строит отчёт+дашборд.

    Рекомендации — общие для отчёта: либо список строк, либо путь к JSON-файлу
    со списком строк (обычно формирует агент-оркестратор после слияния).
    Без них рекомендации выводятся детерминированно из статистики классификаций
    (топ категорий, высокая критичность, доля без оценки), чтобы отчёт и дашборд
    всегда содержали рекомендации; периодные разделы рекомендаций не заполняются.
    """
    output = Path(output_dir)
    work = output / "agents_work"
    manifest_path = work / "manifest.json"
    if not manifest_path.is_file():
        raise AnalysisError(f"Манифест оркестрации не найден: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    schema_version = manifest.get("classification_schema_version", 1)
    service_id = service_id or manifest.get("service_id", "vpn")
    taxonomy = taxonomy or get_taxonomy(service_id)
    billing_category = billing_category or get_billing_category(service_id)

    week_rows = _read_conversations(week_input_path, allow_empty=True)
    day_rows = _read_conversations(day_input_path, allow_empty=True)
    union_rows = _union_conversations(week_rows, day_rows)
    union_payload = _build_payload(union_rows)
    union_ids = [item["id"] for item in union_payload["conversations"]]
    union_id_set = set(union_ids)
    category_names = {item["name"] for item in taxonomy}

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
            if schema_version >= SCHEMA_VERSION:
                try:
                    item = canonical_classification(item, service_id)
                except ValueError as exc:
                    errors.append(f"{conversation_id}: {exc}")
            for field in ("platforms", "additional_topics"):
                values = item.get(field, [])
                if schema_version >= SCHEMA_VERSION and field not in item:
                    errors.append(f"{conversation_id}: отсутствует обязательное поле {field}")
                if not isinstance(values, list) or any(
                    not isinstance(value, str) or not value.strip() for value in values
                ):
                    errors.append(f"{conversation_id}: {field} должен быть списком непустых строк")
            merged[conversation_id] = item
    missing = [cid for cid in union_ids if cid not in merged]
    if missing:
        errors.append(f"не покрыты ID ({len(missing)}): {', '.join(missing[:10])}")
    if errors:
        raise AnalysisError("Ошибки слияния результатов субагентов:\n- " + "\n- ".join(errors))

    review = review_other_classifications(
        merged, union_payload, output, taxonomy, manifest.get("workers", DEFAULT_WORKERS), service_id,
        schema_version=schema_version,
    )
    if review["review_required"]:
        return {**review, "unique_conversation_count": len(union_ids)}
    merged = review["classifications"]
    conversation_by_id = {str(c["id"]): c for c in union_payload["conversations"]}
    if service_id == "hypertube":
        classifications = [
            _finalize_hypertube_classification(merged[cid], conversation_by_id.get(cid))
            for cid in union_ids
        ]
    else:
        classifications = [
            _finalize_classification(merged[cid], conversation_by_id.get(cid))
            for cid in union_ids
        ]
    week_ids = {item["id"] for item in _build_payload(week_rows)["conversations"]}
    day_ids = {item["id"] for item in _build_payload(day_rows)["conversations"]}

    def summary(label: str, ids: set[str]) -> str:
        subset = [item for item in classifications if item["id"] in ids]
        cats = Counter(item["category"] for item in subset)
        sev = Counter(item["severity"] or "unknown" for item in subset)
        top = ", ".join(f"{name} — {count}" for name, count in cats.most_common(3))
        return (f"{label}: {len(subset)} обращений. Топ категорий: {top}. "
                f"Критичность: high {sev.get('high', 0)}, medium {sev.get('medium', 0)}, "
                f"low {sev.get('low', 0)}, не оценена {sev.get('unknown', 0)}.")

    loaded_recommendations = _load_recommendations(recommendations)
    result = {
        "classification_schema_version": schema_version,
        "service_id": service_id,
        "complete": True,
        "conversation_count": len(classifications),
        "dataset_fingerprint": union_payload["dataset_fingerprint"],
        "categories": taxonomy,
        "taxonomy_source": taxonomy_source,
        "classifications": classifications,
        "executive_summary": summary("Объединение", set(union_ids)),
        "recommendations": loaded_recommendations or _default_recommendations(classifications),
        "period_analysis": {
            "week": {
                "conversation_count": len(week_rows),
                "dataset_fingerprint": _build_payload(week_rows)["dataset_fingerprint"],
                "executive_summary": summary("Неделя", week_ids),
                "recommendations": [],
            },
            "day": {
                "conversation_count": len(day_rows),
                "dataset_fingerprint": _build_payload(day_rows)["dataset_fingerprint"],
                "executive_summary": summary("День", day_ids),
                "recommendations": [],
            },
        },
    }
    result_path = output / "chatgpt_result.json"
    _write_json(result_path, result)

    report = generate_monitoring_report(week_input_path, day_input_path, result_path, output,
                                        dashboard_dir=dashboard_dir,
                                        billing_category=billing_category,
                                        fixed_subcategories=get_fixed_subcategories(service_id))
    outcome: dict[str, Any] = {
        "unique_conversation_count": len(classifications),
        "result_path": str(result_path),
        "report_path": report["report_path"],
        "classifications_path": report["classifications_path"],
        "dashboard_path": report["dashboard_path"],
    }
    if "other_review_audit_path" in review:
        outcome["other_review_audit_path"] = review["other_review_audit_path"]
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
        f"| Критичность | {sev_match} | {sev_match / total * 100:.1f}% |",
        f"| Полное совпадение | {full} | {full / total * 100:.1f}% |",
        "", "## Матрица расхождений категорий (топ-20)", "",
        "| Было | Стало | Сколько |", "|---|---|---:|",
    ]
    for (before, after), count in confusion.most_common(20):
        before_esc = str(before).replace("|", "\\|")
        after_esc = str(after).replace("|", "\\|")
        lines.append(f"| {before_esc} | {after_esc} | {count} |")
    lines += ["", "## Список расхождений", "",
              "| ID | Категория была | Категория стала | Подкат. была | Подкат. стала | Критичность была | Критичность стала |",
              "|---|---|---|---|---|---|---|"]
    for row in diffs:
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |")
    comparison_path = output / "comparison_report.md"
    _write_text(comparison_path, "\n".join(lines))
    return comparison_path

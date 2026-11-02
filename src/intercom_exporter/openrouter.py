import hashlib
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

from .support_analysis import (
    AnalysisError,
    _read_conversations,
    _union_conversations,
    _validate_period_analysis,
    _validate_result,
    _write_json,
    generate_monitoring_report,
    generate_report,
)


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DAHL_URL = "https://inference.dahl.global/v1/chat/completions"
OPENROUTER_LABEL = "OpenRouter"
REQUEST_TIMEOUT_SECONDS = 300
MAX_COMPLETION_TOKENS = 64_000
MAX_BATCH_INPUT_BYTES = 400_000
MAX_SINGLE_CONVERSATION_BYTES = 750_000
DEFAULT_OPENROUTER_WORKERS = 3
MAX_TRANSIENT_RETRIES = 2


class OpenRouterError(AnalysisError):
    pass


def _decode_model_result(response_body: Any, provider_label: str = OPENROUTER_LABEL) -> dict[str, Any]:
    try:
        choice = response_body["choices"][0]
        message = choice["message"]
        content = message.get("content")
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError(f"{provider_label} вернул ответ без текста классификации.") from exc
    if isinstance(content, list):
        content = "".join(
            item.get("text", "") for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    if not isinstance(content, str) or not content.strip():
        usage = response_body.get("usage", {})
        completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        details = [
            f"finish_reason={choice.get('finish_reason', 'не указан')}",
            f"completion_tokens={completion_tokens if completion_tokens is not None else 'не указано'}",
            f"model={response_body.get('model', 'не указана')}",
            f"request_id={response_body.get('id', 'не указан')}",
        ]
        refusal = message.get("refusal")
        if refusal:
            details.append("refusal=да")
        raise OpenRouterError(f"{provider_label} вернул пустой ответ классификации (" + ", ".join(details) + ").")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OpenRouterError(f"{provider_label} вернул невалидный JSON; ручные файлы анализа сохранены.") from exc
    if not isinstance(result, dict):
        raise OpenRouterError(f"{provider_label} должен вернуть JSON-объект; ручные файлы анализа сохранены.")
    return result


def _error_message(response: requests.Response | None) -> str:
    if response is None:
        return ""
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error", {}) if isinstance(body, dict) else {}
    message = error.get("message", "") if isinstance(error, dict) else ""
    if not isinstance(message, str):
        return ""
    message = re.sub(r"(?i)bearer\s+\S+", "Bearer [скрыто]", message)
    message = re.sub(r"(?i)\bsk-or-v1-[a-f0-9]{20,}\b", "[ключ скрыт]", message)
    message = re.sub(r"[\r\n\t]+", " ", message).strip()
    return message[:300]


def _request_result(
    api_key: str,
    model: str,
    prompt: str,
    user_content: str,
    *,
    base_url: str = OPENROUTER_URL,
    provider_label: str = OPENROUTER_LABEL,
) -> dict[str, Any]:
    request_data = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": MAX_COMPLETION_TOKENS,
    }
    for attempt in range(MAX_TRANSIENT_RETRIES + 1):
        try:
            response = requests.post(
                base_url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=request_data,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            return _decode_model_result(body, provider_label)
        except requests.Timeout as exc:
            if attempt < MAX_TRANSIENT_RETRIES:
                time.sleep(2 ** attempt)
                continue
            raise OpenRouterError(f"Истекло время ожидания ответа {provider_label}; ручные файлы анализа сохранены.") from exc
        except requests.HTTPError as exc:
            response = exc.response
            status = response.status_code if response is not None else "неизвестен"
            if status in (408, 429, 500, 502, 503, 504) and attempt < MAX_TRANSIENT_RETRIES:
                retry_after = response.headers.get("Retry-After") if response is not None else None
                try:
                    delay = min(float(retry_after), 60) if retry_after else 2 ** attempt
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(max(delay, 0))
                continue
            detail = _error_message(response)
            suffix = f" Подробности {provider_label}: {detail}" if detail else ""
            raise OpenRouterError(
                f"{provider_label} вернул HTTP {status}.{suffix} Ручные файлы анализа сохранены."
            ) from exc
        except requests.RequestException as exc:
            if attempt < MAX_TRANSIENT_RETRIES:
                time.sleep(2 ** attempt)
                continue
            raise OpenRouterError(f"Не удалось выполнить запрос {provider_label}; ручные файлы анализа сохранены: {exc}") from exc
        except ValueError as exc:
            raise OpenRouterError(f"{provider_label} вернул ответ в неизвестном формате; ручные файлы анализа сохранены.") from exc


def _prepared_payload(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpenRouterError(f"Не удалось прочитать подготовленный JSON для AI-анализа: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("conversations"), list):
        raise OpenRouterError(f"Подготовленный JSON имеет неверную структуру: {path}")
    return payload


def _payload_for_conversations(conversations: list[dict[str, Any]]) -> dict[str, Any]:
    digest = hashlib.sha256()
    for index, conversation in enumerate(conversations):
        if index:
            digest.update(b"\n")
        canonical = json.dumps(conversation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest.update(canonical.encode("utf-8"))
    return {
        "conversation_count": len(conversations),
        "dataset_fingerprint": digest.hexdigest(),
        "conversations": conversations,
    }


def _conversation_bytes(conversation: dict[str, Any]) -> int:
    return len(json.dumps(conversation, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _split_batches(
    conversations: list[dict[str, Any]],
    max_bytes: int | None = None,
) -> list[list[dict[str, Any]]]:
    max_bytes = MAX_BATCH_INPUT_BYTES if max_bytes is None else max_bytes
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 0
    for conversation in conversations:
        size = _conversation_bytes(conversation)
        if size > MAX_SINGLE_CONVERSATION_BYTES:
            raise OpenRouterError(
                f"Обращение {conversation.get('id')} превышает безопасный лимит одного пакета "
                f"({size} байт). Подготовленные файлы сохранены."
            )
        if size > max_bytes:
            if current:
                batches.append(current)
                current, current_bytes = [], 0
            batches.append([conversation])
            continue
        if current and current_bytes + size > max_bytes:
            batches.append(current)
            current, current_bytes = [], 0
        current.append(conversation)
        current_bytes += size
    if current:
        batches.append(current)
    return batches


def _classification_policy() -> str:
    template_path = Path(__file__).with_name("support_analysis_prompt_template.md")
    try:
        template = template_path.read_text(encoding="utf-8")
        start = template.index("## 1. Безопасность и единица анализа")
        end = template.index("## 8. Формат `chatgpt_result.json`")
    except (OSError, ValueError) as exc:
        raise OpenRouterError("Не удалось загрузить правила классификации обращений.") from exc
    return template[start:end].strip()


def _batch_prompt(
    batch_payload: dict[str, Any],
    batch_number: int,
    batch_total: int,
    *,
    period_payloads: dict[str, dict[str, Any]] | None = None,
) -> str:
    policy = _classification_policy()
    monitoring_schema = ""
    period_instructions = ""
    if period_payloads is not None:
        period_instructions = """Для каждого обращения во входе указано его членство в периодах в `periods_by_id`. Краткие выводы и рекомендации партии формируй отдельно для week и day, используя только ID соответствующего окна. Даже если в партии ноль записей периода, верни его блок с заданными метаданными, коротким пояснением и пустым списком рекомендаций.

Проверочные метаданные периодов этой партии:
""" + json.dumps({name: {"conversation_count": value["conversation_count"], "dataset_fingerprint": value["dataset_fingerprint"]} for name, value in period_payloads.items()}, ensure_ascii=False)
        monitoring_schema = ', "period_analysis": {"week": {"conversation_count": 0, "dataset_fingerprint": "...", "executive_summary": "...", "recommendations": []}, "day": {"conversation_count": 0, "dataset_fingerprint": "...", "executive_summary": "...", "recommendations": []}}'
    return f"""# Классификация пакета обращений

Это пакет {batch_number} из {batch_total}. Обработай только ID в JSON текущего запроса. Не пытайся дополнять или пересчитывать полный набор из метаданных в общих правилах.
Для этого пакета обязательно верни `conversation_count` = {batch_payload['conversation_count']} и `dataset_fingerprint` = `{batch_payload['dataset_fingerprint']}`. Классифицируй каждый ID пакета ровно один раз. Используй общую фиксированную таксономию из правил ниже; не создавай отдельные названия категорий между пакетами.
Разрешённый список ID этого пакета: {json.dumps([str(item["id"]) for item in batch_payload["conversations"]], ensure_ascii=False)}. В `classifications` можно использовать только эти ID, копируя их посимвольно из входного JSON.

Верни только JSON-объект такой структуры:
{{"complete": true, "conversation_count": {batch_payload['conversation_count']}, "dataset_fingerprint": "{batch_payload['dataset_fingerprint']}", "categories": [{{"name": "...", "description": "..."}}], "classifications": [{{"id": "...", "category": "...", "subcategory": "...", "problem_summary": "...", "severity": null, "severity_reason": "...", "confidence": "low", "evidence": "..."}}], "executive_summary": "Краткий вывод по этой партии", "recommendations": ["..."]{monitoring_schema}}}

Общие числовые рейтинги не придумывай: их посчитает программа после объединения проверенных классификаций.

{period_instructions}

{policy}
"""


def _batch_user_content(
    conversations: list[dict[str, Any]],
    period_membership: dict[str, set[str]] | None = None,
) -> str:
    payload = _payload_for_conversations(conversations)
    if period_membership is not None:
        ids = {str(item["id"]) for item in conversations}
        payload["periods_by_id"] = {
            conversation_id: [name for name, members in period_membership.items() if conversation_id in members]
            for conversation_id in ids
        }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _merge_batch_categories(batch_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    categories: dict[str, str] = {}
    for result in batch_results:
        for category in result["categories"]:
            name, description = category["name"], category["description"]
            categories.setdefault(name, description)
    return [{"name": name, "description": description} for name, description in categories.items()]


def _synthesis_result(
    api_key: str,
    model: str,
    batch_results: list[dict[str, Any]],
    classifications: list[dict[str, Any]],
    categories: list[dict[str, str]],
    dataset_fingerprint: str,
    period_membership: dict[str, set[str]] | None = None,
    period_payloads: dict[str, dict[str, Any]] | None = None,
    *,
    base_url: str = OPENROUTER_URL,
    provider_label: str = OPENROUTER_LABEL,
) -> dict[str, Any]:
    category_counts = Counter(item["category"] for item in classifications)
    severity_counts = Counter(item["severity"] or "unknown" for item in classifications)
    insights = [
        {"executive_summary": item.get("executive_summary", ""), "recommendations": item.get("recommendations", [])}
        for item in batch_results
    ]
    input_data: dict[str, Any] = {
        "conversation_count": len(classifications),
        "dataset_fingerprint": dataset_fingerprint,
        "category_counts": dict(category_counts),
        "severity_counts": dict(severity_counts),
        "categories": categories,
        "batch_insights": insights,
    }
    if period_membership is not None and period_payloads is not None:
        period_insights: dict[str, list[dict[str, Any]]] = {"week": [], "day": []}
        for result in batch_results:
            for name, details in result["period_analysis"].items():
                if details["conversation_count"]:
                    period_insights[name].append({
                        "executive_summary": details["executive_summary"],
                        "recommendations": details["recommendations"],
                    })
        per_period_stats = {}
        by_id = {item["id"]: item for item in classifications}
        for name, ids in period_membership.items():
            period_classifications = [by_id[item] for item in ids]
            per_period_stats[name] = {
                "conversation_count": period_payloads[name]["conversation_count"],
                "dataset_fingerprint": period_payloads[name]["dataset_fingerprint"],
                "category_counts": dict(Counter(item["category"] for item in period_classifications)),
                "severity_counts": dict(Counter(item["severity"] or "unknown" for item in period_classifications)),
                "batch_insights": period_insights[name],
            }
        input_data["period_analysis"] = per_period_stats
        schema = """{"executive_summary":"Общий вывод по объединению","recommendations":["..."],"period_analysis":{"week":{"executive_summary":"...","recommendations":["..."]},"day":{"executive_summary":"...","recommendations":["..."]}}}"""
        instructions = "Подготовь общий вывод и рекомендации, а также отдельные выводы и рекомендации для week и day. Опирайся на статистику и выводы партий; не выдумывай причин или чисел. Верни только поля по схеме."
    else:
        schema = """{"executive_summary":"Общий вывод по обращениям","recommendations":["..."]}"""
        instructions = "Подготовь общий вывод и рекомендации по объединённой статистике и выводам партий. Не выдумывай причин или чисел. Верни только поля по схеме."
    synthesis = _request_result(
        api_key,
        model,
        "Ты готовишь итоговый синтез уже проверенных пакетов классификации. Не меняй категории и не создавай классификации. " + instructions + " Схема: " + schema,
        json.dumps(input_data, ensure_ascii=False, separators=(",", ":")),
        base_url=base_url,
        provider_label=provider_label,
    )
    summary = synthesis.get("executive_summary")
    recommendations = synthesis.get("recommendations")
    if not isinstance(summary, str) or not summary.strip():
        raise OpenRouterError(f"Итоговый синтез {provider_label} не содержит executive_summary.")
    if not isinstance(recommendations, list) or any(not isinstance(item, str) or not item.strip() for item in recommendations):
        raise OpenRouterError(f"Итоговый синтез {provider_label} вернул некорректный recommendations.")
    merged = {
        "complete": True,
        "conversation_count": len(classifications),
        "dataset_fingerprint": dataset_fingerprint,
        "categories": categories,
        "classifications": classifications,
        "executive_summary": summary.strip(),
        "recommendations": recommendations,
    }
    if period_membership is not None and period_payloads is not None:
        merged["period_analysis"] = {}
        period_summaries = synthesis.get("period_analysis")
        if not isinstance(period_summaries, dict):
            raise OpenRouterError(f"Итоговый синтез {provider_label} не содержит period_analysis.")
        for name, payload in period_payloads.items():
            details = period_summaries.get(name)
            if not isinstance(details, dict):
                raise OpenRouterError(f"Итоговый синтез {provider_label} не содержит период {name}.")
            period_summary = details.get("executive_summary")
            period_recommendations = details.get("recommendations")
            if not isinstance(period_summary, str) or not period_summary.strip():
                raise OpenRouterError(f"Итоговый синтез {provider_label} не содержит резюме периода {name}.")
            if not isinstance(period_recommendations, list) or any(
                not isinstance(item, str) or not item.strip() for item in period_recommendations
            ):
                raise OpenRouterError(f"Итоговый синтез {provider_label} вернул некорректные рекомендации периода {name}.")
            merged["period_analysis"][name] = {
                "conversation_count": payload["conversation_count"],
                "dataset_fingerprint": payload["dataset_fingerprint"],
                "executive_summary": period_summary.strip(),
                "recommendations": period_recommendations,
            }
    return merged


def _classify_batches(
    source_rows: list[dict[str, Any]],
    conversations: list[dict[str, Any]],
    api_key: str,
    model: str,
    *,
    period_membership: dict[str, set[str]] | None = None,
    period_payloads: dict[str, dict[str, Any]] | None = None,
    workers: int = DEFAULT_OPENROUTER_WORKERS,
    checkpoint_dir: str | Path | None = None,
    base_url: str = OPENROUTER_URL,
    provider_label: str = OPENROUTER_LABEL,
) -> dict[str, Any]:
    rows_by_id = {str(row["id"]): row for row in source_rows}
    conversations_by_id = {str(row["id"]): row for row in conversations}
    if set(rows_by_id) != set(conversations_by_id):
        raise OpenRouterError("Подготовленный JSON и исходная выгрузка содержат разные ID.")
    batches = _split_batches(conversations)
    batch_results: list[dict[str, Any] | None] = [None] * len(batches)
    batch_classifications: list[list[dict[str, Any]] | None] = [None] * len(batches)

    def process_batch(index: int, batch: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
        batch_payload = _payload_for_conversations(batch)
        batch_rows = [rows_by_id[item] for item in (str(row["id"]) for row in batch)]
        batch_period_payloads = None
        if period_membership is not None:
            batch_period_payloads = {
                name: _payload_for_conversations([item for item in batch if str(item["id"]) in ids])
                for name, ids in period_membership.items()
            }
        prompt = _batch_prompt(
            batch_payload, index, len(batches), period_payloads=batch_period_payloads,
        )
        user_content = _batch_user_content(batch, period_membership)
        cache_path = None
        if checkpoint_dir is not None:
            cache_key = hashlib.sha256(json.dumps(
                {"model": model, "provider": provider_label, "prompt": prompt, "user_content": user_content},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            cache_path = Path(checkpoint_dir) / f"batch_{cache_key}.json"

        def validate_batch_result(response_result: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            categories, classifications = _validate_result(batch_rows, batch_payload, response_result)
            if period_membership is not None and batch_period_payloads is not None:
                validated_periods = _validate_period_analysis(
                    response_result, batch_period_payloads["week"], batch_period_payloads["day"],
                )
                response_result["period_analysis"] = {
                    name: {
                        **details,
                        "conversation_count": batch_period_payloads[name]["conversation_count"],
                        "dataset_fingerprint": batch_period_payloads[name]["dataset_fingerprint"],
                    }
                    for name, details in validated_periods.items()
                }
            response_result["categories"] = categories
            response_result["classifications"] = classifications
            return response_result, classifications

        cached = False
        result = None
        if cache_path is not None and cache_path.is_file():
            try:
                result = json.loads(cache_path.read_text(encoding="utf-8"))
                if not isinstance(result, dict):
                    raise AnalysisError("Кэш пакета содержит не JSON-объект.")
                result, classifications = validate_batch_result(result)
                cached = True
            except (OSError, json.JSONDecodeError, AnalysisError, TypeError):
                result = None

        for attempt in (range(2) if result is None else ()):
            current_prompt = prompt
            if attempt:
                allowed_ids = [str(item["id"]) for item in batch]
                current_prompt += (
                    "\n\nПОВТОРНАЯ ПОПЫТКА: предыдущий ответ не прошёл строгую проверку: "
                    + str(validation_error)
                    + " Исправь ответ, используя каждый ID ровно один раз. Допустимые ID: "
                    + json.dumps(allowed_ids, ensure_ascii=False)
                    + " Верни полный JSON в той же схеме, без пояснений."
                )
                print(f"{provider_label}: повтор пакета {index}/{len(batches)} после ошибки проверки")
            response_result = _request_result(
                api_key, model, current_prompt, user_content,
                base_url=base_url, provider_label=provider_label,
            )
            try:
                result, classifications = validate_batch_result(response_result)
                break
            except AnalysisError as exc:
                validation_error = exc
                if attempt:
                    raise OpenRouterError(
                        f"Пакет {index}/{len(batches)} не прошел проверку и после повтора: {exc}"
                    ) from exc
        if result is None:
            raise OpenRouterError(f"Не удалось обработать пакет {index}/{len(batches)}.")
        if cache_path is not None and not cached:
            _write_json(cache_path, result)
        return result, classifications, cached

    completed = 0
    if batches:
        worker_count = max(1, min(workers, len(batches)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_indices = {}
            for index, batch in enumerate(batches, start=1):
                print(f"{provider_label}: отправка пакета {index}/{len(batches)} ({len(batch)} обращений, {sum(_conversation_bytes(item) for item in batch)} байт)")
                future = executor.submit(process_batch, index, batch)
                future_indices[future] = index - 1
            for future in as_completed(future_indices):
                index = future_indices[future]
                batch_result, classifications, cached = future.result()
                batch_results[index] = batch_result
                batch_classifications[index] = classifications
                completed += 1
                label = "из кэша" if cached else "проверен"
                print(f"{provider_label}: готово пакетов {completed}/{len(batches)} ({label})")

    final_batch_results = [item for item in batch_results if item is not None]
    final_batch_classifications = [item for item in batch_classifications if item is not None]

    categories = _merge_batch_categories(final_batch_results)
    classifications = [item for group in final_batch_classifications for item in group]
    input_payload = _payload_for_conversations(conversations)
    if len(batches) == 1:
        final_result = final_batch_results[0]
        final_result.update({
            "complete": True,
            "conversation_count": len(conversations),
            "dataset_fingerprint": input_payload["dataset_fingerprint"],
            "categories": categories,
            "classifications": classifications,
        })
    else:
        print(f"{provider_label}: объединение {len(batches)} пакетов и синтез общих выводов")
        final_result = _synthesis_result(
            api_key, model, final_batch_results, classifications, categories,
            input_payload["dataset_fingerprint"], period_membership, period_payloads,
            base_url=base_url, provider_label=provider_label,
        )
    if period_membership is not None and period_payloads is not None:
        _validate_period_analysis(final_result, period_payloads["week"], period_payloads["day"])
    return final_result


def run_standard_analysis(
    source_path: str | Path,
    output_dir: str | Path,
    api_key: str,
    model: str,
    workers: int = DEFAULT_OPENROUTER_WORKERS,
    *,
    base_url: str = OPENROUTER_URL,
    provider_label: str = OPENROUTER_LABEL,
) -> dict[str, Any]:
    output = Path(output_dir)
    prepared = _prepared_payload(output / "chatgpt_input.json")
    source_rows = _read_conversations(source_path)
    result = _classify_batches(
        source_rows, prepared["conversations"], api_key, model,
        workers=workers, checkpoint_dir=output / ".openrouter-cache",
        base_url=base_url, provider_label=provider_label,
    )
    result_path = output / "chatgpt_result.json"
    _write_json(result_path, result)
    try:
        report = generate_report(source_path, result_path, output)
    except AnalysisError as exc:
        raise OpenRouterError(
            f"Ответ {provider_label} не прошел проверку полноты или схемы: {exc}. "
            "Ручные файлы анализа сохранены."
        ) from exc
    return {**report, "result_path": str(result_path)}


def run_monitoring_analysis(
    week_path: str | Path,
    day_path: str | Path,
    output_dir: str | Path,
    api_key: str,
    model: str,
    workers: int = DEFAULT_OPENROUTER_WORKERS,
    *,
    base_url: str = OPENROUTER_URL,
    provider_label: str = OPENROUTER_LABEL,
) -> dict[str, Any]:
    output = Path(output_dir)
    week_payload = _prepared_payload(output / "chatgpt_input_week.json")
    day_payload = _prepared_payload(output / "chatgpt_input_day.json")
    source_week_rows = _read_conversations(week_path, allow_empty=True)
    source_day_rows = _read_conversations(day_path, allow_empty=True)
    source_rows = _union_conversations(source_week_rows, source_day_rows)
    week_by_id = {str(item["id"]): item for item in week_payload["conversations"]}
    day_by_id = {str(item["id"]): item for item in day_payload["conversations"]}
    conversations_by_id = {**week_by_id}
    for conversation_id, conversation in day_by_id.items():
        if conversation_id in conversations_by_id and conversations_by_id[conversation_id] != conversation:
            raise OpenRouterError(f"Подготовленные недельный и дневной входы расходятся для ID {conversation_id}.")
        conversations_by_id.setdefault(conversation_id, conversation)
    conversations = list(conversations_by_id.values())
    period_membership = {
        "week": set(week_by_id),
        "day": set(day_by_id),
    }
    period_payloads = {
        "week": _payload_for_conversations(week_payload["conversations"]),
        "day": _payload_for_conversations(day_payload["conversations"]),
    }
    result = _classify_batches(
        source_rows,
        conversations,
        api_key,
        model,
        period_membership=period_membership,
        period_payloads=period_payloads,
        workers=workers,
        checkpoint_dir=output / ".openrouter-cache",
        base_url=base_url,
        provider_label=provider_label,
    )
    result_path = output / "chatgpt_result.json"
    _write_json(result_path, result)
    try:
        report = generate_monitoring_report(week_path, day_path, result_path, output)
    except AnalysisError as exc:
        raise OpenRouterError(
            f"Ответ {provider_label} не прошел проверку полноты или схемы: {exc}. "
            "Ручные файлы анализа сохранены."
        ) from exc
    return {**report, "result_path": str(result_path)}

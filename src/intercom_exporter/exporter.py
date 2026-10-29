import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dates import DateRange
from .storage import JsonlStore

logger = logging.getLogger(__name__)


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds + 0.5))
    hours, remaining = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remaining, 60)
    return f"{hours} ч {minutes} мин {seconds} сек"


class ConversationExporter:
    def __init__(self, client, *, max_workers: int = 4):
        if max_workers < 1:
            raise ValueError("max_workers должен быть не меньше 1")
        self.client = client
        self.max_workers = max_workers

    def run(self, date_range: DateRange, output_root: str | Path, *, api_version: str = "unknown") -> dict[str, Any]:
        output_dir = Path(output_root) / f"{date_range.start.isoformat()}_{date_range.end.isoformat()}"
        store = JsonlStore(output_dir / "conversations.jsonl")
        found = exported = errors = 0
        started_monotonic = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        limitations: list[str] = []
        seen_ids: set[str] = set()

        def in_period(timestamp) -> bool:
            if timestamp is None:
                return False
            try:
                return date_range.contains(int(timestamp))
            except (TypeError, ValueError, OverflowError):
                return False

        def records():
            nonlocal found, exported, errors

            def candidate_batches():
                nonlocal found, errors
                batch = []
                for candidate in self.client.search_conversations(date_range.start, date_range.end):
                    conversation_id = candidate.get("id")
                    if conversation_id is None:
                        errors += 1
                        limitations.append("Найдено обращение без идентификатора; его невозможно выгрузить.")
                        continue
                    conversation_id = str(conversation_id)
                    if conversation_id in seen_ids:
                        continue
                    seen_ids.add(conversation_id)
                    candidate_matches = in_period(candidate.get("created_at")) or in_period(candidate.get("updated_at"))
                    if candidate_matches:
                        found += 1
                    batch.append((candidate, conversation_id, candidate_matches))
                    if len(batch) == self.max_workers:
                        yield batch
                        batch = []
                if batch:
                    yield batch

            try:
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    for batch in candidate_batches():
                        futures = [executor.submit(self.client.get_conversation, item[1]) for item in batch]
                        for (candidate, conversation_id, candidate_matches), future in zip(batch, futures):
                            try:
                                detail = future.result()
                                parts_container = detail.get("conversation_parts") or {}
                                parts = parts_container.get("conversation_parts", []) if isinstance(parts_container, dict) else []
                                if not isinstance(parts, list):
                                    raise ValueError("Некорректный список частей переписки")
                                candidate_statistics = candidate.get("statistics")
                                detail_statistics = detail.get("statistics")
                                expected_parts = max(
                                    int(candidate.get("count_conversation_parts") or (candidate_statistics or {}).get("count_conversation_parts") or 0),
                                    int((detail_statistics or {}).get("count_conversation_parts") or 0),
                                )
                                response_part_count = int(parts_container.get("total_count") or 0) if isinstance(parts_container, dict) else 0
                                available_count = len(parts) + (1 if detail.get("source") else 0)
                                truncated = expected_parts > available_count or response_part_count > len(parts)
                                if truncated:
                                    known_count = max(expected_parts, response_part_count + (1 if detail.get("source") else 0))
                                    limitations.append(f"Обращение {conversation_id}: API вернул не всю историю (доступно {available_count}, ожидается {known_count} частей); активность в периоде может быть не обнаружена.")
                                    errors += 1

                                part_matches = any(
                                    isinstance(part, dict) and in_period(part.get("created_at"))
                                    for part in parts
                                )
                                detail_matches = in_period(detail.get("created_at")) or in_period(detail.get("updated_at"))
                                if not candidate_matches and not detail_matches and not part_matches:
                                    if truncated:
                                        limitations.append(f"Обращение {conversation_id}: невозможно проверить продолжение в периоде из-за лимита истории API.")
                                    continue

                                if not candidate_matches:
                                    found += 1
                                exported += 1
                                yield self._normalize(detail, parts)
                            except Exception as exc:
                                errors += 1
                                limitations.append(f"Не удалось получить полную историю одного обращения ({type(exc).__name__}).")
                                logger.error("Не удалось получить обращение (%s)", type(exc).__name__)
            except Exception as exc:
                errors += 1
                limitations.append(f"Получение списка обращений завершилось ошибкой: {type(exc).__name__}.")
                logger.error("Ошибка получения списка обращений (%s)", type(exc).__name__)

        store.merge(records())
        elapsed_seconds = time.perf_counter() - started_monotonic
        manifest = {
            "start": date_range.start.isoformat(),
            "end_exclusive": date_range.end.isoformat(),
            "started_at": started_at,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "api_version": api_version,
            "found_count": found,
            "exported_count": exported,
            "error_count": errors,
            "status": "success" if errors == 0 else "partial",
            "limitations": limitations,
        }
        store.write_manifest(manifest)
        logger.info("Обращений найдено: %d; успешно выгружено: %d; ошибок: %d; время: %s.", found, exported, errors, format_duration(elapsed_seconds))
        return {**manifest, "output_dir": str(output_dir)}

    @staticmethod
    def _normalize(conversation: dict, parts: list) -> dict:
        result: dict[str, Any] = {}
        for field in ("id", "type", "title", "created_at", "updated_at", "state", "open", "read", "priority",
                      "waiting_since", "snoozed_until", "admin_assignee_id", "team_assignee_id", "tags", "custom_attributes"):
            if field in conversation:
                result[field] = conversation[field]
        statistics = conversation.get("statistics")
        if isinstance(statistics, dict):
            result["statistics"] = {
                key: statistics[key]
                for key in ("last_close_at", "count_conversation_parts")
                if key in statistics
            }
        source = conversation.get("source")
        if isinstance(source, dict):
            attachment_fields = ("name", "url", "type", "content_type", "filesize")
            source_fields = {key: source[key] for key in ("type", "id", "delivered_as", "subject", "body", "url", "redacted") if key in source}
            author = source.get("author")
            if isinstance(author, dict):
                source_fields["author"] = {key: author[key] for key in ("id", "type", "name") if key in author}
            attachments = source.get("attachments")
            if isinstance(attachments, list):
                source_fields["attachments"] = [
                    {key: attachment[key] for key in attachment_fields if key in attachment}
                    for attachment in attachments if isinstance(attachment, dict)
                ]
            if source_fields:
                result["source"] = source_fields
        contacts = conversation.get("contacts")
        if isinstance(contacts, dict):
            contact_items = contacts.get("contacts")
            if isinstance(contact_items, list):
                result["contacts"] = {
                    "type": contacts.get("type"),
                    "contacts": [
                        {key: contact[key] for key in ("id", "type") if key in contact}
                        for contact in contact_items if isinstance(contact, dict)
                    ],
                }
        normalized_parts = []
        part_fields = (
            "id", "part_type", "created_at", "updated_at", "notified_at", "type", "body",
            "redacted", "assigned_to", "admin_assignee_id", "team_assignee_id", "external_id",
            "state", "metadata", "tags", "event_details", "app_package_code", "translations",
        )
        attachment_fields = ("name", "url", "type", "content_type", "filesize")
        for part in parts:
            if not isinstance(part, dict):
                continue
            item = {key: part[key] for key in part_fields if key in part}
            author = part.get("author")
            if isinstance(author, dict):
                item["author"] = {key: author[key] for key in ("id", "type", "name") if key in author}
            attachments = part.get("attachments")
            if isinstance(attachments, list):
                item["attachments"] = [
                    {key: attachment[key] for key in attachment_fields if key in attachment}
                    for attachment in attachments if isinstance(attachment, dict)
                ]
            normalized_parts.append(item)
        result["conversation_parts"] = normalized_parts
        return ConversationExporter._convert_timestamps_to_utc(result)

    @staticmethod
    def _convert_timestamps_to_utc(value: Any, field_name: str | None = None) -> Any:
        if isinstance(value, dict):
            return {
                key: ConversationExporter._convert_timestamps_to_utc(item, key)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [ConversationExporter._convert_timestamps_to_utc(item) for item in value]
        if (
            field_name is not None
            and (field_name.endswith(("_at", "_until")) or field_name == "waiting_since")
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return value

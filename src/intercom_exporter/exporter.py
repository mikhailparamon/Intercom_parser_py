import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dates import DateRange
from .storage import JsonlStore

logger = logging.getLogger(__name__)


class ConversationExporter:
    def __init__(self, client):
        self.client = client

    def run(self, date_range: DateRange, output_root: str | Path, *, api_version: str = "unknown") -> dict[str, Any]:
        output_dir = Path(output_root) / f"{date_range.start.isoformat()}_{date_range.end.isoformat()}"
        store = JsonlStore(output_dir / "conversations.jsonl")
        found = exported = errors = 0
        started_at = datetime.now(timezone.utc).isoformat()
        limitations: list[str] = []

        def records():
            nonlocal found, exported, errors
            try:
                for candidate in self.client.search_conversations(date_range.start, date_range.end):
                    created_at = candidate.get("created_at")
                    if created_at is None or not date_range.contains(int(created_at)):
                        continue
                    found += 1
                    conversation_id = candidate.get("id")
                    if conversation_id is None:
                        errors += 1
                        limitations.append("Найдено обращение без идентификатора; его невозможно выгрузить.")
                        continue
                    try:
                        detail = self.client.get_conversation(str(conversation_id))
                        parts_container = detail.get("conversation_parts") or {}
                        parts = parts_container.get("conversation_parts", []) if isinstance(parts_container, dict) else []
                        expected_parts = int(
                            candidate.get("count_conversation_parts")
                            or (candidate.get("statistics") or {}).get("count_conversation_parts")
                            or 0
                        )
                        response_part_count = int(parts_container.get("total_count") or 0) if isinstance(parts_container, dict) else 0
                        available_count = len(parts) + (1 if detail.get("source") else 0)
                        if expected_parts > available_count or response_part_count > len(parts):
                            known_count = max(expected_parts, response_part_count + (1 if detail.get("source") else 0))
                            limitations.append(f"Обращение {conversation_id}: API вернул не всю историю (доступно {available_count}, ожидается {known_count} частей).")
                            errors += 1
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
        manifest = {
            "start": date_range.start.isoformat(),
            "end_exclusive": date_range.end.isoformat(),
            "started_at": started_at,
            "api_version": api_version,
            "found_count": found,
            "exported_count": exported,
            "error_count": errors,
            "status": "success" if errors == 0 else "partial",
            "limitations": limitations,
        }
        store.write_manifest(manifest)
        logger.info("Обращений найдено: %d; успешно выгружено: %d; ошибок: %d", found, exported, errors)
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
        return result

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .dates import DateRange
from .storage import JsonStore

logger = logging.getLogger(__name__)


def resolve_timezone(timezone_name: str):
    normalized = timezone_name.strip()
    if normalized.casefold() == "unix":
        return None
    if normalized.casefold() == "utc":
        return timezone.utc
    try:
        return ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Неизвестный часовой пояс: {timezone_name}") from exc


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds + 0.5))
    hours, remaining = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remaining, 60)
    return f"{hours} ч {minutes} мин {seconds} сек"


class ConversationExporter:
    def __init__(self, client, *, max_workers: int = 15):
        if max_workers < 1:
            raise ValueError("max_workers должен быть не меньше 1")
        self.client = client
        self.max_workers = max_workers

    @staticmethod
    def _create_output_dir(output_root: str | Path, date_range: DateRange,
                           started_at: datetime) -> Path:
        root = Path(output_root)
        root.mkdir(parents=True, exist_ok=True)
        base_name = f"{date_range.start.isoformat()}_{date_range.end.isoformat()}"
        output_dir = root / base_name
        try:
            output_dir.mkdir()
            return output_dir
        except FileExistsError:
            run_stamp = started_at.strftime("%Y%m%dT%H%M%S.%fZ")
            unique_name = f"{base_name}_run_{run_stamp}"
            output_dir = root / unique_name
            suffix = 1
            while True:
                try:
                    output_dir.mkdir()
                    return output_dir
                except FileExistsError:
                    output_dir = root / f"{unique_name}_{suffix:02d}"
                    suffix += 1

    def run(self, date_range: DateRange, output_root: str | Path, *, api_version: str = "unknown",
            selection: str = "all", fields: str = "important", timezone_name: str = "UTC") -> dict[str, Any]:
        if selection not in {"new", "updated", "all"}:
            raise ValueError("selection должен быть new, updated или all")
        if fields not in {"important", "all"}:
            raise ValueError("fields должен быть important или all")
        target_timezone = resolve_timezone(timezone_name)
        timezone_label = "unix" if target_timezone is None else timezone_name
        started_monotonic = time.perf_counter()
        started_at_time = datetime.now(timezone.utc)
        output_dir = self._create_output_dir(output_root, date_range, started_at_time)
        store = JsonStore(output_dir / "conversations.json")
        found = exported = errors = 0
        started_at = started_at_time.isoformat()
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
                    batch.append((candidate, conversation_id))
                    if len(batch) == self.max_workers:
                        yield batch
                        batch = []
                if batch:
                    yield batch

            try:
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    for batch in candidate_batches():
                        futures = [executor.submit(self.client.get_conversation, item[1]) for item in batch]
                        for (candidate, conversation_id), future in zip(batch, futures):
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

                                part_matches = any(
                                    isinstance(part, dict) and in_period(part.get("created_at"))
                                    for part in parts
                                )
                                created_at = candidate.get("created_at", detail.get("created_at"))
                                created_matches = in_period(created_at) or in_period(detail.get("created_at"))
                                updated_matches = in_period(candidate.get("updated_at")) or in_period(detail.get("updated_at"))
                                if selection == "new":
                                    selected = created_matches
                                elif selection == "updated":
                                    try:
                                        is_old = created_at is not None and int(created_at) < date_range.epoch_bounds()[0]
                                    except (TypeError, ValueError, OverflowError):
                                        is_old = False
                                    selected = is_old and (updated_matches or part_matches)
                                else:
                                    selected = created_matches or updated_matches or part_matches
                                if not selected:
                                    if truncated and selection != "new" and not created_matches:
                                        known_count = max(expected_parts, response_part_count + (1 if detail.get("source") else 0))
                                        limitations.append(f"Обращение {conversation_id}: невозможно проверить продолжение в периоде из-за лимита истории API (доступно {available_count}, ожидается {known_count} частей).")
                                        errors += 1
                                    continue

                                if truncated:
                                    known_count = max(expected_parts, response_part_count + (1 if detail.get("source") else 0))
                                    limitations.append(f"Обращение {conversation_id}: API вернул не всю историю (доступно {available_count}, ожидается {known_count} частей); экспортированная история неполна.")
                                    errors += 1

                                found += 1
                                exported += 1
                                yield self._normalize(detail, parts, fields=fields,
                                                      target_timezone=target_timezone)
                            except Exception as exc:
                                errors += 1
                                limitations.append(f"Не удалось получить полную историю одного обращения ({type(exc).__name__}).")
                                logger.error("Не удалось получить обращение (%s)", type(exc).__name__)
            except Exception as exc:
                errors += 1
                limitations.append(f"Получение списка обращений завершилось ошибкой: {type(exc).__name__}.")
                logger.error("Ошибка получения списка обращений (%s)", type(exc).__name__)

        store.replace(records())
        elapsed_seconds = time.perf_counter() - started_monotonic
        manifest = {
            "start": date_range.start.isoformat(),
            "end_exclusive": date_range.end.isoformat(),
            "started_at": started_at,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "api_version": api_version,
            "selection": selection,
            "fields": fields,
            "timezone": timezone_label,
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
    def _normalize(conversation: dict, parts: list, *, fields: str = "all",
                   target_timezone: Any = timezone.utc) -> dict:
        if fields == "important":
            result = {
                key: conversation[key]
                for key in ("id", "title", "created_at", "updated_at")
                if key in conversation
            }
            source = conversation.get("source")
            if isinstance(source, dict) and "body" in source:
                result["source"] = {"body": source["body"]}
            result["conversation_parts"] = [
                {
                    **{key: part[key] for key in ("part_type", "created_at", "body") if key in part},
                    **({"author": {"type": part["author"]["type"]}}
                       if isinstance(part.get("author"), dict) and "type" in part["author"] else {}),
                }
                for part in parts if isinstance(part, dict)
            ]
            return ConversationExporter._convert_timestamps(result, target_timezone)

        return ConversationExporter._convert_timestamps(conversation, target_timezone)

    @staticmethod
    def _convert_timestamps(value: Any, target_timezone: Any,
                            field_name: str | None = None) -> Any:
        if isinstance(value, dict):
            return {
                key: ConversationExporter._convert_timestamps(item, target_timezone, key)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [ConversationExporter._convert_timestamps(item, target_timezone) for item in value]
        if (
            target_timezone is not None
            and field_name is not None
            and (field_name.endswith(("_at", "_until")) or field_name == "waiting_since")
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            converted = datetime.fromtimestamp(value, timezone.utc).astimezone(target_timezone)
            rendered = converted.isoformat(timespec="seconds")
            if converted.utcoffset() == timezone.utc.utcoffset(converted):
                return rendered.replace("+00:00", "Z")
            return rendered
        return value

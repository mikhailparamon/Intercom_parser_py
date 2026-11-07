import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .config import load_agent_workers
from .services import DEFAULT_SERVICE_ID, get_service, iter_services


class AnalysisError(ValueError):
    pass


_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_URL_RE = re.compile(
    r"(?i)(?:https?://|www\.)[^\s<>()]+|"
    r"(?<![@\w])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?:/[^\s<>()]*)?"
)
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+\d{1,3}(?:[ \t().-]*\d){7,14}|"
    r"(?:8|7)(?:[ \t().-]*\d){10}|"
    r"\d{3}[ \t-]\d{3}[ \t-]\d{2}[ \t-]\d{2})(?!\w)"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?P<label>\b(?:пароль|password|token|токен|api[\s_-]?key|access[\s_-]?token|"
    r"secret|секрет|ключ[\s_-]+доступа)\b\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/-]+=*")
_KNOWN_TOKEN_RE = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,})\b"
)
_SIGNOFF_RE = re.compile(
    r"(?im)^[ \t]*(?:с уважением|с наилучшими пожеланиями|best regards|kind regards|regards)"
    r"[ \t,!.:-]*[^\r\n]*(?:\r?\n[\s\S]*)?\Z"
)
_DEVICE_SIGNATURE_RE = re.compile(
    r"(?im)^[ \t]*(?:sent from my (?:iphone|ipad|android|mobile device)|"
    r"отправлено с (?:моего )?(?:iphone|ipad|android))[ \t.!,-]*[^\r\n]*(?:\r?\n)?"
)
_SEVERITIES = {"low", "medium", "high"}
_CONFIDENCE = {"low", "medium", "high"}
OTHER_CATEGORY = "Прочие обращения"
DOCS_TARIFF_SUBCATEGORY = "Ограничение тарифа «Для документов»"
OTHER_SUBCATEGORIES = (
    "Нет запроса", "Автоматическое сообщение", "Спам", "Не по теме сервиса",
    "Только вложение без описания", "Недостаточно контекста",
)


def _docs_tariff_operator_evidence(conversation: dict | None) -> list[str]:
    """Find explicit app restrictions confirmed by an operator, not mere plan mentions."""
    evidence = []
    for message in (conversation or {}).get("messages", []):
        if message.get("role") != "operator":
            continue
        body = message.get("text", "")
        text = re.sub(r"<[^>]+>", " ", body).casefold()
        text = re.sub(r"[^\w\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        if not re.search(r"для документ\w*", text) or "приложени" not in text:
            continue
        explicit_denial = re.search(
            r"приложени.{0,120}(?:не предусмотр|не доступ|недоступ|работать не будут|не будут работать)", text,
        )
        higher_plan_required = "для видео" in text and re.search(
            r"приложени.{0,100}работает (?:только )?(?:на тариф|начиная|только при подключении)", text,
        )
        minimum_plan = re.search(
            r"чтобы работало.{0,40}приложени.{0,80}нужна подписка минимум", text,
        )
        if explicit_denial or higher_plan_required or minimum_plan:
            evidence.append(body)
    return evidence


def _normalize_problem_subcategory(value: str) -> str:
    """Normalize exact aliases while keeping platforms out of problem labels."""
    lowered = value.lower().strip().strip('"').strip("'").replace("—", "-")
    aliases = {
        "отмена": "Отмена подписки",
        "отмены": "Отмена подписки",
        "отмена подписки": "Отмена подписки",
        "отменить подписку": "Отмена подписки",
        "отмените подписку": "Отмена подписки",
        "отмени подписку": "Отмена подписки",
        "cancel subscription": "Отмена подписки",
        "cancel my subscription": "Отмена подписки",
        "не приходит код": "Код не приходит",
        "код не поступает": "Код не приходит",
        "видео не грузится": "Видео не загружается",
        "видео незагружается": "Видео не загружается",
        "не подключается vpn": "VPN не подключается",
        "vpn не подключается": "VPN не подключается",
        "сайт не открывается": "Сайт или сервис недоступен",
        "сайт недоступен": "Сайт или сервис недоступен",
        "сервис недоступен": "Сайт или сервис недоступен",
        "сервис не открывается": "Сайт или сервис недоступен",
    }
    return aliases.get(lowered, value.strip())


def _normalize_platforms(values: list[str], context: str = "") -> list[str]:
    """Collapse platform aliases to stable product, OS, and browser groups."""
    result: list[str] = []
    context_lower = context.casefold()
    full_evidence = f"{' '.join(values).casefold()} {context_lower}"
    desktop_os = (
        (r"\b(?:windows|win\s*10|win\s*11)\b", "Windows"),
        (r"\b(?:macos|mac\s*os|macbook|imac)\b", "macOS"),
        (r"\blinux\b", "Linux"),
    )

    def add(value: str) -> None:
        if value not in result:
            result.append(value)

    for raw in values:
        value = raw.strip()
        lowered = value.casefold()
        evidence = f"{lowered} {context_lower}"

        if "openvpn" in lowered:
            add("OpenVPN")
            for pattern, platform in (
                (r"\b(?:ios|iphone|ipad)\b", "iOS"),
                (r"\bandroid\b", "Android"),
                (r"\bwindows(?:\s*(?:10|11))?\b", "Windows"),
                (r"\b(?:macos|mac\s*os|macbook|imac)\b", "macOS"),
                (r"\blinux\b", "Linux"),
            ):
                if re.search(pattern, lowered):
                    add(platform)
            continue

        if (
            "vpn наоборот" in lowered or "vpnnaoborot" in lowered or "naoborot app" in lowered
            or (lowered == "приложение" and "vpn наоборот" in context_lower)
        ):
            add("Приложение VPN Наоборот")
            continue

        if re.search(r"\b(?:windows|win\s*10|win\s*11)\b", lowered):
            add("Windows")
            continue
        if re.search(r"\b(?:macos|mac\s*os|macbook|imac)\b", lowered):
            add("macOS")
            continue
        if re.search(r"\blinux\b", lowered):
            add("Linux")
            continue
        if re.search(r"\b(?:ios|iphone|ipad)\b", lowered):
            add("iOS")
            continue
        if re.search(r"\bandroid\b", lowered):
            add("Android")
            continue

        if lowered in {"телефон", "phone", "mobile phone"}:
            if re.search(r"\b(?:ios|iphone|ipad)\b", evidence):
                add("iOS")
            elif re.search(r"\bandroid\b", evidence):
                add("Android")
            continue

        if lowered in {
            "app store", "apple app store", "play store", "google play",
            "египет", "egypt", "разные устройства", "несколько устройств",
        }:
            continue

        is_extension = "расширен" in lowered or "extension" in lowered or lowered == "chrome"
        if is_extension:
            matched = False
            if re.search(r"\b(?:яндекс|yandex)\b", evidence):
                add("Расширение Яндекс")
                matched = True
            if re.search(r"\b(?:chrome|google chrome)\b", evidence):
                add("Расширение Chrome")
                matched = True
            if not matched and (
                re.search(r"\b(?:firefox|mozilla|edge|opera|safari|brave|vivaldi)\b", evidence)
                or "другого браузера" in lowered or "другой браузер" in lowered
            ):
                add("Расширение другого браузера")
                matched = True
            if not matched:
                add("Расширение браузера (браузер не указан)")
            continue

        if re.search(r"\b(?:компьютер|пк|ноутбук|laptop|desktop)\b", lowered):
            detected_os = [platform for pattern, platform in desktop_os if re.search(pattern, full_evidence)]
            if detected_os:
                for platform in detected_os:
                    add(platform)
            else:
                add("Компьютер (ОС не указана)")
            continue

        if lowered in {"приложение vpn наоборот", "vpn наоборот app"}:
            add("Приложение VPN Наоборот")
        else:
            add(value)
    return result


def _remove_url(match: re.Match) -> str:
    value = match.group(0)
    suffix = ""
    while value and value[-1] in ".,;:!?":
        suffix = value[-1] + suffix
        value = value[:-1]
    return suffix


def sanitize_text(value: str) -> str:
    """Delete specified identifiers/signatures while preserving all other text."""
    text = _SIGNOFF_RE.sub("", value)
    text = _DEVICE_SIGNATURE_RE.sub("", text)
    text = _EMAIL_RE.sub("", text)
    text = _URL_RE.sub(_remove_url, text)
    text = _SECRET_VALUE_RE.sub("", text)
    text = _BEARER_RE.sub(lambda match: match.group(1), text)
    text = _KNOWN_TOKEN_RE.sub("", text)
    text = _PHONE_RE.sub("", text)
    return text


def _read_conversations(path: str | Path, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    input_path = Path(path)
    if not input_path.is_file():
        raise AnalysisError(f"Файл выгрузки не найден: {input_path}")
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def append_row(row: Any, location: str) -> None:
        if not isinstance(row, dict):
            raise AnalysisError(f"В {location} ожидался объект обращения")
        raw_id = row.get("id")
        if raw_id is None or not str(raw_id).strip():
            raise AnalysisError(f"В {location} нет ID обращения")
        conversation_id = str(raw_id)
        if conversation_id in seen_ids:
            raise AnalysisError(f"В выгрузке повторяющийся ID обращения: {conversation_id}")
        seen_ids.add(conversation_id)
        rows.append(row)

    try:
        with input_path.open("r", encoding="utf-8-sig") as source:
            first_character = ""
            while character := source.read(1):
                if not character.isspace():
                    first_character = character
                    break
            source.seek(0)
            if first_character == "[":
                try:
                    document = json.load(source)
                except json.JSONDecodeError as exc:
                    raise AnalysisError("Некорректный JSON-массив обращений") from exc
                if not isinstance(document, list):
                    raise AnalysisError("Ожидался JSON-массив обращений")
                for index, row in enumerate(document, start=1):
                    append_row(row, f"обращении с индексом {index}")
            else:
                for line_number, line in enumerate(source, start=1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise AnalysisError(f"Некорректный JSON в строке {line_number}") from exc
                    append_row(row, f"строке {line_number}")
    except OSError as exc:
        raise AnalysisError(f"Не удалось прочитать файл выгрузки: {input_path}") from exc
    if not rows and not allow_empty:
        raise AnalysisError("Файл выгрузки не содержит обращений")
    return rows


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _build_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    conversations = []
    for row in rows:
        messages = []
        source = row.get("source")
        if isinstance(source, dict):
            body = _text(source.get("body"))
            clean = sanitize_text(body)
            if clean.strip():
                messages.append({"role": "customer", "part_type": "source", "text": clean})
        parts = row.get("conversation_parts")
        if isinstance(parts, dict):
            parts = parts.get("conversation_parts")
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                body = _text(part.get("body"))
                clean = sanitize_text(body)
                if not clean.strip():
                    continue
                author = part.get("author")
                author_type = author.get("type") if isinstance(author, dict) else None
                role = {"user": "customer", "admin": "operator", "bot": "bot"}.get(author_type, "unknown")
                messages.append({
                    "role": role,
                    "part_type": _text(part.get("part_type")) or "unknown",
                    "text": clean,
                })
        conversations.append({
            "id": str(row["id"]),
            "title": sanitize_text(_text(row.get("title"))),
            "messages": messages,
        })
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


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                         prefix=f".{path.stem}-", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                         prefix=f".{path.stem}-", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def _prompt(payload: dict[str, Any], input_file_bytes: int) -> str:
    from .classification_catalog import catalog_prompt
    template_path = Path(__file__).with_name("support_analysis_prompt_template.md")
    template = template_path.read_text(encoding="utf-8")
    values = {
        "[[CONVERSATION_COUNT]]": f"{payload['conversation_count']:,}".replace(",", " "),
        "[[DATASET_FINGERPRINT]]": payload["dataset_fingerprint"],
        "[[INPUT_FILE_BYTES]]": str(input_file_bytes),
        "[[INPUT_FILE_MB]]": f"{input_file_bytes / (1024 * 1024):.1f}",
    }
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    return template.replace("[[SUBCATEGORY_CATALOG]]", catalog_prompt("vpn"))


def prepare_analysis(input_path: str | Path, output_dir: str | Path | None = None) -> dict[str, Any]:
    rows = _read_conversations(input_path)
    payload = _build_payload(rows)
    output = Path(output_dir) if output_dir else Path(input_path).parent / "support-analysis"
    input_file = output / "chatgpt_input.json"
    _write_json(input_file, payload)
    input_file_bytes = input_file.stat().st_size
    _write_text(output / "PROMPT.md", _prompt(payload, input_file_bytes))
    message_count = sum(len(conversation["messages"]) for conversation in payload["conversations"])
    return {
        "conversation_count": len(rows),
        "message_count": message_count,
        "input_file_bytes": input_file_bytes,
        "output_dir": str(output),
        "dataset_fingerprint": payload["dataset_fingerprint"],
    }


def _union_conversations(week_rows: list[dict], day_rows: list[dict]) -> list[dict]:
    union: dict[str, dict] = {}
    for row in [*week_rows, *day_rows]:
        conversation_id = str(row["id"])
        if conversation_id in union and union[conversation_id] != row:
            raise AnalysisError(f"ID {conversation_id} имеет разное содержимое в недельном и дневном файлах")
        union.setdefault(conversation_id, row)
    return list(union.values())


def _monitoring_prompt(week: dict, day: dict, combined: dict, week_bytes: int, day_bytes: int, service_id: str | None = None) -> str:
    from .classification_catalog import catalog_prompt
    template_name = "support_analysis_prompt_template_hypertube.md" if service_id == "hypertube" else "support_analysis_prompt_template.md"
    template_path = Path(__file__).with_name(template_name)
    template = template_path.read_text(encoding="utf-8")
    start = template.index("## 1. Безопасность и единица анализа")
    end = template.index("## 8. Формат `chatgpt_result.json`")
    classification_policy = template[start:end].strip().replace("[[SUBCATEGORY_CATALOG]]", catalog_prompt(service_id or "vpn"))
    week_ids = {item["id"] for item in week["conversations"]}
    day_ids = {item["id"] for item in day["conversations"]}
    overlap = len(week_ids & day_ids)
    return f"""# Единый анализ мониторинговых окон

Проанализируй **оба** входных файла: `chatgpt_input_week.json` и `chatgpt_input_day.json`.
Недельное окно: {week.get('period', 'последние 7×24 часа')}; {week['conversation_count']} обращений (fingerprint `{week['dataset_fingerprint']}`, {week_bytes} байт). Дневное окно: {day.get('period', 'последние 24 часа')}; {day['conversation_count']} обращений (fingerprint `{day['dataset_fingerprint']}`, {day_bytes} байт). Совпадают {overlap} ID. Объединение содержит {combined['conversation_count']} уникальных ID (fingerprint `{combined['dataset_fingerprint']}`).

## Правило объединённого анализа

- Проверь фактические `conversation_count`, fingerprint и ID **в каждом** файле.
- Объедини наборы по `id`. Совпадающие записи классифицируй **один раз**, используя один общий набор категорий; классификации не дублируй по периодам.
- Определи членство ID в окнах по фактическим множествам входных файлов. Не предполагай, что дневной файл всегда является подмножеством недельного: сообщи о расхождении, если это не так.
- Классифицируй все уникальные ID объединения. Подготовь отдельные резюме и рекомендации для недели и дня, опираясь только на записи соответствующего файла. Не складывай недельные и дневные количества: дневные обращения входят в недельный период и образуют пересечение.
- Количество, рейтинг категорий, доли и тяжесть считай программно по классификациям и членству ID. Не выдумывай динамику внутри недели, которой нельзя установить по этим двум снимкам.

Создай **один комплект** результатов в доступных для скачивания файлах:

1. `chatgpt_result.json` — единый список классификаций для уникальных ID, общая таксономия и отдельные summary/recommendations для каждого окна.
2. `classifications.csv` — одна строка на уникальный ID и колонка `periods` со значениями `week`, `day` или `week;day`.
3. `report.md` — единый отчёт с отдельными выводами за неделю и день, сравнительной таблицей категорий, размером пересечения и явным предупреждением, что дневные обращения входят в недельные.

## Формат `chatgpt_result.json`

```json
{{
  "complete": true,
  "classification_schema_version": 2,
  "service_id": "{service_id or 'vpn'}",
  "conversation_count": {combined['conversation_count']},
  "dataset_fingerprint": "{combined['dataset_fingerprint']}",
  "categories": [{{"name": "...", "description": "..."}}],
  "classifications": [
    {{"id": "...", "category": "...", "subcategory_code": "...", "subcategory": "...", "additional_topics": [], "platforms": [], "problem_summary": "...", "severity": null, "severity_reason": "...", "confidence": "low", "evidence": "..."}}
  ],
  "executive_summary": "Общий краткий вывод по объединению",
  "recommendations": ["Общая рекомендация"],
  "period_analysis": {{
    "week": {{"conversation_count": {week['conversation_count']}, "dataset_fingerprint": "{week['dataset_fingerprint']}", "executive_summary": "Вывод только по недельному набору", "recommendations": ["..."]}},
    "day": {{"conversation_count": {day['conversation_count']}, "dataset_fingerprint": "{day['dataset_fingerprint']}", "executive_summary": "Вывод только по дневному набору", "recommendations": ["..."]}}
  }}
}}
```

В `classifications` должна быть ровно одна запись на каждый уникальный ID объединения. Категории должны быть общими для двух окон. `period_analysis.week/day` обязаны сохранять соответствующие число обращений и fingerprint из фактического файла. Не объявляй результат полным при пропущенных ID.

{classification_policy}

## Требования к единому `report.md`

- В начале укажи фактические имена обоих файлов, их размеры, количество записей и fingerprint, число уникальных ID объединения и размер пересечения.
- Раздели частоты, тяжесть, резюме и рекомендации на **последние 7 дней** и **последний день**. Добавь сравнение количества и долей категорий между окнами.
- В каждой таблице долю рассчитывай от числа обращений соответствующего окна. Дневные значения не прибавляй к недельным и не представляй окна как непересекающиеся.
- Включи ограничения методики и явно укажи, что классификация автоматическая/эвристическая, если нет полной экспертной проверки.
- Используй один общий набор классификаций; примеры и цитаты должны быть безопасными и анонимизированными.

## Финальная проверка

Убедись, что результат покрывает уникальное объединение ID ровно один раз; `conversation_count` и fingerprint объединения правильны; у каждого окна совпадают его фактические количество и fingerprint; CSV имеет те же ID и корректное членство; итоговый единый `report.md` содержит выводы для обоих окон. Не печатай классификацию тысяч обращений в чат.
"""


def _default_monitoring_output(week_path: Path, day_path: Path) -> Path:
    """Каталог анализа по умолчанию: <общий родитель>/support-analysis для стандартной
    раскладки last_week/last_day, иначе support-analysis рядом с недельным файлом."""
    if week_path.parent.name == "last_week" and day_path.parent.name == "last_day" and week_path.parent.parent == day_path.parent.parent:
        return week_path.parent.parent / "support-analysis"
    return week_path.parent / "support-analysis"


def find_latest_run(data_root: str | Path = "data", service_id: str | None = None) -> Path:
    """Каталог последнего прогона мониторинга.

    Новая раскладка: data/<stamp>/<service_folder>/last_week/...
    Для обратной совместимости: старая раскладка data/<service_id>/<stamp>/last_week/...
    и самая старая data/<stamp>/last_week/... (только vpn).
    """
    root = Path(data_root)
    service = get_service(service_id)
    candidates = []

    # Новая раскладка: data/<stamp>/<service_folder>/last_week/...
    if root.is_dir():
        for path in root.iterdir():
            if not path.is_dir():
                continue
            service_dir = path / service.folder_name
            if (service_dir / "last_week" / "conversations.json").is_file() and (service_dir / "last_day" / "conversations.json").is_file():
                candidates.append(service_dir)

    # Старая раскладка: data/<service_id>/<stamp>/last_week/...
    legacy_root = root / service.id
    if legacy_root.is_dir():
        for path in legacy_root.iterdir():
            if path.is_dir() and (path / "last_week" / "conversations.json").is_file() and (path / "last_day" / "conversations.json").is_file():
                candidates.append(path)

    # Самая старая раскладка: data/<stamp>/last_week/... (только vpn)
    if service.id == "vpn":
        for path in root.iterdir():
            if path.is_dir() and (path / "last_week" / "conversations.json").is_file() and (path / "last_day" / "conversations.json").is_file():
                candidates.append(path)

    if not candidates:
        raise AnalysisError(f"В {root} нет прогонов со срезами last_week/last_day для сервиса {service.id}")

    def stamp(path: Path) -> float:
        marker = path / "last_day" / "manifest.json"
        try:
            return marker.stat().st_mtime
        except OSError:
            return path.stat().st_mtime

    return max(candidates, key=stamp)


def _resolve_run_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    """Разрешает пары срезов: явные --week/--day, --run либо последний прогон в data/."""
    provided = [args.week is not None, args.day is not None]
    if any(provided) and not all(provided):
        raise AnalysisError("Указывайте --week и --day вместе, либо используйте --run/последний прогон")
    if all(provided):
        if args.run:
            raise AnalysisError("--run нельзя сочетать с --week/--day")
        return Path(args.week), Path(args.day)
    if args.run:
        run = Path(args.run)
    else:
        service = getattr(args, "service", None)
        run = find_latest_run(service_id=service)
    return run / "last_week" / "conversations.json", run / "last_day" / "conversations.json"


def prepare_monitoring_analysis(
    week_input_path: str | Path,
    day_input_path: str | Path,
    output_dir: str | Path | None = None,
    service_id: str | None = None,
) -> dict[str, Any]:
    week_path, day_path = Path(week_input_path), Path(day_input_path)
    week_rows = _read_conversations(week_path, allow_empty=True)
    day_rows = _read_conversations(day_path, allow_empty=True)
    union_rows = _union_conversations(week_rows, day_rows)
    if not union_rows:
        raise AnalysisError("Недельный и дневной срезы не содержат обращений")
    week_payload = _build_payload(week_rows)
    day_payload = _build_payload(day_rows)
    week_payload["period"] = _period_label(week_path) or "Последние 7×24 часа UTC"
    day_payload["period"] = _period_label(day_path) or "Последние 24 часа UTC"
    combined_payload = _build_payload(union_rows)
    if output_dir:
        output = Path(output_dir)
    else:
        output = _default_monitoring_output(week_path, day_path)
    week_file = output / "chatgpt_input_week.json"
    day_file = output / "chatgpt_input_day.json"
    _write_json(week_file, week_payload)
    _write_json(day_file, day_payload)
    prompt = _monitoring_prompt(
        week_payload, day_payload, combined_payload,
        week_file.stat().st_size, day_file.stat().st_size,
        service_id=service_id,
    )
    _write_text(output / "PROMPT.md", prompt)
    return {
        "week_conversation_count": len(week_rows),
        "day_conversation_count": len(day_rows),
        "unique_conversation_count": len(union_rows),
        "overlap_count": len({str(row["id"]) for row in week_rows} & {str(row["id"]) for row in day_rows}),
        "output_dir": str(output),
        "week_fingerprint": week_payload["dataset_fingerprint"],
        "day_fingerprint": day_payload["dataset_fingerprint"],
        "dataset_fingerprint": combined_payload["dataset_fingerprint"],
    }


def _load_result(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise AnalysisError(f"Не удалось прочитать результат ChatGPT: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AnalysisError("Результат ChatGPT должен быть корректным JSON") from exc
    if not isinstance(value, dict):
        raise AnalysisError("Результат ChatGPT должен быть JSON-объектом")
    return value


def _validate_result(rows: list[dict[str, Any]], payload: dict[str, Any], result: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    from .classification_catalog import SCHEMA_VERSION, canonical_classification, confirmed_platforms, get_subcategories

    strict = result.get("classification_schema_version", 1) >= SCHEMA_VERSION
    service_id = result.get("service_id", "vpn")
    if result.get("complete") is not True:
        raise AnalysisError("Результат помечен как неполный (complete не равен true)")
    if result.get("conversation_count") != len(rows):
        raise AnalysisError(f"conversation_count в результате должен быть равен {len(rows)}")
    if result.get("dataset_fingerprint") != payload["dataset_fingerprint"]:
        raise AnalysisError("dataset_fingerprint не совпадает с подготовленным набором; проверьте, что это результат анализа текущего файла")
    raw_categories = result.get("categories")
    if not isinstance(raw_categories, list) or not raw_categories:
        raise AnalysisError("В результате ChatGPT нет списка categories")
    categories = []
    category_names = set()
    for item in raw_categories:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise AnalysisError("У каждой категории должны быть name и description")
        name = item["name"].strip()
        if name in category_names:
            raise AnalysisError(f"Повторяется категория: {name}")
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            raise AnalysisError(f"У категории «{name}» нет description")
        category_names.add(name)
        categories.append({"name": name, "description": description.strip()})

    raw_classifications = result.get("classifications")
    if not isinstance(raw_classifications, list):
        raise AnalysisError("В результате ChatGPT нет списка classifications")
    expected_ids = {str(row["id"]) for row in rows}
    safe_conversations = {
        str(conversation["id"]): conversation
        for conversation in payload.get("conversations", [])
        if isinstance(conversation, dict) and conversation.get("id") is not None
    }
    classifications = []
    seen_ids: set[str] = set()
    required_text = ("category", "subcategory", "problem_summary", "severity_reason")
    for item in raw_classifications:
        if not isinstance(item, dict) or item.get("id") is None:
            raise AnalysisError("У классификации отсутствует ID")
        if strict:
            try:
                item = canonical_classification(item, service_id)
            except ValueError as exc:
                raise AnalysisError(f"У обращения {item['id']}: {exc}") from exc
        conversation_id = str(item["id"])
        if conversation_id not in expected_ids:
            raise AnalysisError(f"Результат содержит неизвестный ID обращения: {conversation_id}")
        if conversation_id in seen_ids:
            raise AnalysisError(f"ID обращения повторяется в классификации: {conversation_id}")
        seen_ids.add(conversation_id)
        for field in required_text:
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise AnalysisError(f"У обращения {conversation_id} не заполнено поле {field}")
        if not isinstance(item.get("evidence"), str):
            raise AnalysisError(f"У обращения {conversation_id} поле evidence должно быть строкой")
        platforms = item.get("platforms", [])
        if strict and any(field not in item for field in ("platforms", "additional_topics")):
            raise AnalysisError(f"У обращения {conversation_id} обязательны platforms и additional_topics")
        if not isinstance(platforms, list) or any(not isinstance(value, str) or not value.strip() for value in platforms):
            raise AnalysisError(f"У обращения {conversation_id}: platforms должен быть списком непустых строк")
        additional_topics = item.get("additional_topics", [])
        if not isinstance(additional_topics, list) or any(
            not isinstance(value, str) or not value.strip() for value in additional_topics
        ):
            raise AnalysisError(f"У обращения {conversation_id}: additional_topics должен быть списком непустых строк")
        if item["category"] not in category_names:
            raise AnalysisError(f"У обращения {conversation_id} неизвестная категория: {item['category']}")
        severity = item.get("severity")
        if severity is not None and (not isinstance(severity, str) or severity not in _SEVERITIES):
            raise AnalysisError(f"У обращения {conversation_id} неверное severity: {severity}")
        confidence = item.get("confidence")
        if not isinstance(confidence, str) or confidence not in _CONFIDENCE:
            raise AnalysisError(f"У обращения {conversation_id} неверное confidence: {confidence}")
        safe_conversation = safe_conversations.get(conversation_id, {})
        additional_topics = list(additional_topics)
        if (
            _normalize_problem_subcategory(item["subcategory"]) != DOCS_TARIFF_SUBCATEGORY
            and DOCS_TARIFF_SUBCATEGORY not in additional_topics
            and _docs_tariff_operator_evidence(safe_conversation)
        ):
            additional_topics.append(DOCS_TARIFF_SUBCATEGORY)
        customer_context = " ".join(
            message.get("text", "") for message in safe_conversation.get("messages", [])
            if isinstance(message, dict) and message.get("role") == "customer"
        )
        classifications.append({
            **({"subcategory_code": item["subcategory_code"]} if "subcategory_code" in item else {}),
            "id": conversation_id,
            "category": item["category"],
            "subcategory": _normalize_problem_subcategory(item["subcategory"]),
            "problem_summary": item["problem_summary"].strip(),
            "severity": severity,
            "severity_reason": item["severity_reason"].strip(),
            "confidence": confidence,
            "evidence": item["evidence"].strip(),
            "platforms": confirmed_platforms(safe_conversation, service_id) if strict else _normalize_platforms(platforms, customer_context),
            "additional_topics": list(dict.fromkeys(value.strip() for value in additional_topics)),
        })
    missing_ids = expected_ids - seen_ids
    if missing_ids:
        raise AnalysisError(f"В классификации пропущены обращения: {', '.join(sorted(missing_ids))}")
    if len(classifications) != len(rows):
        raise AnalysisError(f"Ожидалось {len(rows)} классификаций, получено {len(classifications)}")
    if strict:
        allowed_categories = {name for entry in get_subcategories(service_id) for name in entry["categories"]}
        if category_names != allowed_categories:
            raise AnalysisError("Список categories должен точно совпадать с десятью категориями сервиса")
    row_order = {str(row["id"]): index for index, row in enumerate(rows)}
    classifications.sort(key=lambda item: row_order[item["id"]])
    summary = result.get("executive_summary")
    if not isinstance(summary, str) or not summary.strip():
        raise AnalysisError("В результате ChatGPT нет executive_summary")
    recommendations = result.get("recommendations")
    if not isinstance(recommendations, list) or any(not isinstance(value, str) or not value.strip() for value in recommendations):
        raise AnalysisError("recommendations должен быть списком непустых строк")
    return categories, classifications


def _period_label(input_path: str | Path) -> str | None:
    manifest_path = Path(input_path).parent / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    start = manifest.get("start")
    end = manifest.get("end_exclusive")
    if isinstance(start, str) and isinstance(end, str):
        return f"[{start}, {end}) UTC"
    return None


def _markdown_report(rows: list[dict], categories: list[dict], classifications: list[dict], result: dict,
                    period: str | None) -> tuple[str, dict, dict]:
    from .dashboard import build_dashboard_data, coverage_notes

    aggregates = build_dashboard_data(rows, [], categories, classifications, period or "", "")
    sub_note, platform_note = coverage_notes(aggregates)
    category_counts = Counter(item["category"] for item in classifications)
    severity_counts = Counter(item["severity"] or "unknown" for item in classifications)
    severity_summary = {key: severity_counts.get(key, 0) for key in ("high", "medium", "low", "unknown")}
    descriptions = {item["name"]: item["description"] for item in categories}
    lines = ["# Анализ обращений службы поддержки", ""]
    if period:
        lines.extend([f"Период выгрузки: {period}", ""])
    lines.extend([
        f"Обращений проанализировано: **{len(rows)}**. Подсчёты выполнены по уникальным ID обращений.",
        "", "## Резюме", "", result["executive_summary"].strip(), "", "## Частота проблем", "",
        "| Место | Категория | Обращений | Доля | Описание |", "|---:|---|---:|---:|---|",
    ])
    ranked = sorted(category_counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    for rank, (name, count) in enumerate(ranked, start=1):
        share = count / len(rows) * 100
        description = descriptions.get(name, "").replace("|", "\\|").replace("\n", " ")
        escaped_name = name.replace("|", "\\|")
        lines.append(f"| {rank} | {escaped_name} | {count} | {share:.1f}% | {description} |")
    subcategory_counts = Counter(item["subcategory"] for item in classifications)
    lines.extend([
        "", "## Топ-10 подкатегорий: проблемы и запросы", "",
        "| Место | Подкатегория | Обращений | Доля |", "|---:|---|---:|---:|",
    ])
    for rank, entry in enumerate(aggregates["subcategories"], start=1):
        name, count = entry["name"], entry["count"]
        escaped_name = name.replace("|", "\\|")
        lines.append(f"| {rank} | {escaped_name} | {count} | {count / len(rows) * 100:.1f}% |")
    lines.extend(["", sub_note, "", "### Полное распределение подкатегорий", "", "| Подкатегория | Обращений |", "|---|---:|"])
    lines.extend(f"| {name} | {count} |" for name, count in subcategory_counts.most_common())
    platform_counts = Counter(
        platform for item in classifications for platform in set(item.get("platforms", []))
    )
    lines.extend(["", "## Платформы", ""])
    if platform_counts:
        lines.extend(["| Платформа | Обращений |", "|---|---:|"])
        lines.extend(
            f"| {name} | {count} |"
            for name, count in sorted(platform_counts.items(), key=lambda pair: (-pair[1], pair[0].casefold()))
        )
    else:
        lines.append("Платформа не указана или не подтверждена в переписке.")
    lines.extend(["", platform_note, "", "Одно обращение может учитываться в нескольких платформах: способах подключения, операционных системах, устройствах и браузерах. Сумма строк может превышать число обращений."])
    docs_tariff_cases = [
        item for item in classifications
        if item["subcategory"] == DOCS_TARIFF_SUBCATEGORY
        or DOCS_TARIFF_SUBCATEGORY in item.get("additional_topics", [])
    ]
    lines.extend(["", "## Ограничение тарифа «Для документов»", ""])
    lines.append(f"Подтверждено обращений: **{len(docs_tariff_cases)}**.")
    lines.append("Считаются уникальные ID по всей сохранённой переписке, включая подтверждения оператора. Ограничение может быть основной или дополнительной темой; повторные сообщения не увеличивают количество обращений.")
    if docs_tariff_cases:
        lines.extend(["", "Примеры:", ""])
        lines.extend(
            f"- `{item['id']}` — {item['problem_summary']}"
            for item in docs_tariff_cases[:5]
        )
    other_cases = [item for item in classifications if item["category"] == OTHER_CATEGORY]
    lines.extend(["", "## Что входит в «Прочие обращения»", ""])
    if other_cases:
        lines.extend(["| Основание | Обращений |", "|---|---:|"])
        for name, count in Counter(item["subcategory"] for item in other_cases).most_common():
            escaped_name = name.replace("|", "\\|")
            lines.append(f"| {escaped_name} | {count} |")
        lines.extend(["", "Примеры оставшихся случаев:", ""])
        lines.extend(f"- `{item['id']}` — {item['subcategory']}: {item['problem_summary']}" for item in other_cases[:5])
    else:
        lines.append("Обращений в категории «Прочие обращения» нет.")
    lines.extend(["", "## Тяжесть по текстовым признакам", "", "| Уровень | Обращений |", "|---|---:|"])
    for key, label in (("high", "Высокая"), ("medium", "Средняя"), ("low", "Низкая"), ("unknown", "Не оценена")):
        lines.append(f"| {label} | {severity_summary[key]} |")
    high_cases = [item for item in classifications if item["severity"] == "high"]
    if high_cases:
        lines.extend(["", "### Обращения с высокой тяжестью", ""])
        for item in high_cases:
            lines.append(f"- `{item['id']}` — {item['category']} / {item['subcategory']}: {item['problem_summary']} (уверенность: {item['confidence']}; основание: {item['severity_reason']})")
    if result["recommendations"]:
        lines.extend(["", "## Рекомендации", ""])
        lines.extend(f"- {value.strip()}" for value in result["recommendations"])
    lines.extend(["", "## Ограничения", "", "Тяжесть — качественная оценка по тексту обращений. Она не измеряет число затронутых клиентов, финансовый ущерб или технический масштаб проблемы.", ""])
    return "\n".join(lines), dict(ranked), severity_summary


def generate_report(input_path: str | Path, result_path: str | Path,
                    output_dir: str | Path | None = None) -> dict[str, Any]:
    rows = _read_conversations(input_path)
    payload = _build_payload(rows)
    result = _load_result(result_path)
    categories, classifications = _validate_result(rows, payload, result)
    period = _period_label(input_path)
    markdown, category_counts, severity_counts = _markdown_report(rows, categories, classifications, result, period)
    output = Path(output_dir) if output_dir else Path(input_path).parent / "support-analysis"
    _write_text(output / "report.md", markdown)
    csv_path = output / "classifications.csv"
    _write_classifications_csv(csv_path, classifications)
    return {
        "conversation_count": len(rows),
        "category_counts": category_counts,
        "severity_counts": severity_counts,
        "report_path": str(output / "report.md"),
        "classifications_path": str(csv_path),
    }


def _write_classifications_csv(path: Path, classifications: list[dict], periods: dict[str, set[str]] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ("id", "category", "subcategory", "additional_topics", "platforms", "problem_summary", "severity",
              "severity_reason", "confidence", "evidence")
    if any("subcategory_code" in item for item in classifications):
        fields = (*fields, "subcategory_code")
    if periods is not None:
        fields = (*fields, "periods")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", newline="", dir=path.parent,
                                         prefix=".classifications-", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for item in classifications:
                row = dict(item)
                row["additional_topics"] = ";".join(item.get("additional_topics", []))
                row["platforms"] = ";".join(item.get("platforms", []))
                if periods is not None:
                    conversation_id = item["id"]
                    row["periods"] = ";".join(
                        label for label, ids in (("week", periods["week"]), ("day", periods["day"]))
                        if conversation_id in ids
                    )
                writer.writerow(row)
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def _validate_period_analysis(result: dict, week_payload: dict, day_payload: dict) -> dict[str, dict]:
    periods = result.get("period_analysis")
    if not isinstance(periods, dict):
        raise AnalysisError("В результате отсутствует period_analysis для недели и дня")
    expected = {"week": week_payload, "day": day_payload}
    validated = {}
    for name, payload in expected.items():
        period = periods.get(name)
        if not isinstance(period, dict):
            raise AnalysisError(f"В результате отсутствует period_analysis.{name}")
        if period.get("conversation_count") != payload["conversation_count"]:
            raise AnalysisError(f"conversation_count окна {name} не совпадает с входным файлом")
        if period.get("dataset_fingerprint") != payload["dataset_fingerprint"]:
            raise AnalysisError(f"dataset_fingerprint окна {name} не совпадает с входным файлом")
        summary = period.get("executive_summary")
        if not isinstance(summary, str) or not summary.strip():
            raise AnalysisError(f"В period_analysis.{name} отсутствует executive_summary")
        recommendations = period.get("recommendations")
        if not isinstance(recommendations, list) or any(not isinstance(item, str) or not item.strip() for item in recommendations):
            raise AnalysisError(f"recommendations окна {name} должны быть списком непустых строк")
        validated[name] = {"executive_summary": summary.strip(), "recommendations": recommendations}
    return validated


def render_dashboard(
    week_input_path: str | Path,
    day_input_path: str | Path,
    result_path: str | Path,
    output_dir: str | Path | None = None,
    dashboard_dir: str | Path | None = None,
    billing_category: str | None = None,
    fixed_subcategories: list[str] | None = None,
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
        billing_category=billing_category,
        fixed_subcategories=fixed_subcategories,
    )
    output = Path(output_dir) if output_dir else _default_monitoring_output(week_path, day_path)
    dashboard_path = write_dashboard(data, Path(dashboard_dir) if dashboard_dir else output)
    return {"dashboard_path": str(dashboard_path)}


def generate_monitoring_report(
    week_input_path: str | Path,
    day_input_path: str | Path,
    result_path: str | Path,
    output_dir: str | Path | None = None,
    dashboard_dir: str | Path | None = None,
    billing_category: str | None = None,
    fixed_subcategories: list[str] | None = None,
) -> dict[str, Any]:
    week_path, day_path = Path(week_input_path), Path(day_input_path)
    week_rows = _read_conversations(week_path, allow_empty=True)
    day_rows = _read_conversations(day_path, allow_empty=True)
    union_rows = _union_conversations(week_rows, day_rows)
    if not union_rows:
        raise AnalysisError("Недельный и дневной срезы не содержат обращений")
    week_payload, day_payload = _build_payload(week_rows), _build_payload(day_rows)
    union_payload = _build_payload(union_rows)
    result = _load_result(result_path)
    categories, classifications = _validate_result(union_rows, union_payload, result)
    period_analysis = _validate_period_analysis(result, week_payload, day_payload)

    week_ids = {str(row["id"]) for row in week_rows}
    day_ids = {str(row["id"]) for row in day_rows}
    classifications_by_id = {item["id"]: item for item in classifications}
    week_classifications = [classifications_by_id[item] for item in classifications_by_id if item in week_ids]
    day_classifications = [classifications_by_id[item] for item in classifications_by_id if item in day_ids]
    week_label = _period_label(week_path) or "Последние 7 дней"
    day_label = _period_label(day_path) or "Последний день"
    week_markdown, week_counts, _ = _markdown_report(
        week_rows, categories, week_classifications, period_analysis["week"], week_label,
    )
    day_markdown, day_counts, _ = _markdown_report(
        day_rows, categories, day_classifications, period_analysis["day"], day_label,
    )
    week_markdown = week_markdown.split("\n", 1)[-1].lstrip()
    day_markdown = day_markdown.split("\n", 1)[-1].lstrip()

    category_names = [item["name"] for item in categories]
    lines = [
        "# Сводный анализ обращений поддержки: неделя и день", "",
        f"Недельный набор: **{len(week_rows)}** обращений; дневной набор: **{len(day_rows)}** обращений; "
        f"уникальное объединение: **{len(union_rows)}**; пересечение окон: **{len(week_ids & day_ids)}**.", "",
        "Дневные обращения входят в недельный набор. Периодные показатели приведены раздельно и не суммируются.", "",
        "## Сравнение периодов", "",
        "| Категория | За неделю | Доля недели | За день | Доля дня |", "|---|---:|---:|---:|---:|",
    ]
    for name in category_names:
        week_count, day_count = week_counts.get(name, 0), day_counts.get(name, 0)
        week_share = f"{week_count / len(week_rows) * 100:.1f}%" if week_rows else "—"
        day_share = f"{day_count / len(day_rows) * 100:.1f}%" if day_rows else "—"
        lines.append(f"| {name} | {week_count} | {week_share} | {day_count} | {day_share} |")
    lines.extend(["", "## Последние 7 дней", "", week_markdown, "", "## Последний день", "", day_markdown, ""])
    lines.extend(["## Рекомендации", ""])
    lines.extend(f"- {value.strip()}" for value in result["recommendations"])
    report_markdown = "\n".join(lines)

    if output_dir:
        output = Path(output_dir)
    else:
        output = _default_monitoring_output(week_path, day_path)
    report_path = output / "report.md"
    _write_text(report_path, report_markdown)
    csv_path = output / "classifications.csv"
    _write_classifications_csv(csv_path, classifications, {"week": week_ids, "day": day_ids})
    # Ленивый импорт: dashboard импортирует _write_text из этого модуля — импорт наверху дал бы цикл.
    from .dashboard import build_dashboard_data, write_dashboard

    dashboard_data = build_dashboard_data(
        week_rows, day_rows, categories, classifications, week_label, day_label,
        {
            "overall": result["recommendations"],
            "week": period_analysis["week"]["recommendations"],
            "day": period_analysis["day"]["recommendations"],
        },
        billing_category=billing_category,
        fixed_subcategories=fixed_subcategories,
    )
    dashboard_path = write_dashboard(dashboard_data, Path(dashboard_dir) if dashboard_dir else output)
    return {
        "unique_conversation_count": len(union_rows),
        "week_conversation_count": len(week_rows),
        "day_conversation_count": len(day_rows),
        "overlap_count": len(week_ids & day_ids),
        "report_path": str(report_path),
        "classifications_path": str(csv_path),
        "dashboard_path": str(dashboard_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Подготовка, AI-анализ и проверка результатов по обращениям поддержки")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="Очистить и подготовить набор для ChatGPT")
    prepare.add_argument("--input", required=True, help="Исходный conversations.json или conversations.jsonl")
    prepare.add_argument("--output", help="Каталог анализа (по умолчанию: support-analysis рядом с входным файлом)")
    report = subparsers.add_parser("report", help="Проверить классификации ChatGPT и построить отчёт")
    report.add_argument("--input", required=True, help="Исходный conversations.json или conversations.jsonl")
    report.add_argument("--result", required=True, help="JSON-результат ChatGPT")
    report.add_argument("--output", help="Каталог отчёта (по умолчанию: support-analysis рядом с входным файлом)")
    monitor_prepare = subparsers.add_parser("prepare-monitoring", help="Подготовить недельный и дневной наборы с одним общим промптом")
    monitor_prepare.add_argument("--week", required=True, help="Недельный conversations.json")
    monitor_prepare.add_argument("--day", required=True, help="Дневной conversations.json")
    monitor_prepare.add_argument("--output", help="Общий каталог с двумя входными файлами и PROMPT.md")
    monitor_prepare.add_argument("--workers", type=int,
                                 help="Число параллельных субагентов для пакета оркестрации (2–20; по умолчанию: AGENT_WORKERS из .env или 10)")
    monitor_prepare.add_argument("--no-agents", action="store_true",
                                 help="Не создавать пакет оркестрации субагентов (только PROMPT.md и входные JSON)")
    monitor_prepare.add_argument("--service", help="Сервис (vpn или hypertube); по умолчанию vpn")
    monitor_report = subparsers.add_parser("report-monitoring", help="Проверить общий результат и построить единый отчёт по двум окнам")
    monitor_report.add_argument("--week", required=True, help="Исходный conversations.json недельного окна")
    monitor_report.add_argument("--day", required=True, help="Исходный conversations.json дневного окна")
    monitor_report.add_argument("--result", required=True, help="Единый chatgpt_result.json")
    monitor_report.add_argument("--output", help="Общий каталог для report.md и classifications.csv")
    monitor_report.add_argument("--dashboard-dir", help="Каталог для dashboard.html (по умолчанию: общий каталог отчёта)")
    merge = subparsers.add_parser("merge-agents", help="Слить результаты субагентов, проверить и построить отчёт с дашбордом")
    merge.add_argument("--week", help="Исходный conversations.json недельного окна (по умолчанию: последний прогон)")
    merge.add_argument("--day", help="Исходный conversations.json дневного окна (по умолчанию: последний прогон)")
    merge.add_argument("--run", help="Каталог прогона в data/ (по умолчанию: последний); альтернатива --week/--day")
    merge.add_argument("--output", help="Каталог анализа (по умолчанию: support-analysis рядом с данными)")
    merge.add_argument("--compare-with", help="Прошлый chatgpt_result.json для отчёта сравнения")
    merge.add_argument("--recommendations", help="JSON-файл со списком общих рекомендаций для отчёта и дашборда")
    merge.add_argument("--dashboard-dir", help="Каталог для dashboard.html (по умолчанию: каталог отчёта)")
    merge.add_argument("--service", help="Обработать только один сервис (vpn или hypertube); по умолчанию все")
    dash = subparsers.add_parser("render-dashboard", help="Построить dashboard.html из готового chatgpt_result.json без слияния")
    dash.add_argument("--week", help="Исходный conversations.json недельного окна (по умолчанию: последний прогон)")
    dash.add_argument("--day", help="Исходный conversations.json дневного окна (по умолчанию: последний прогон)")
    dash.add_argument("--run", help="Каталог прогона в data/ (по умолчанию: последний)")
    dash.add_argument("--result", help="Готовый chatgpt_result.json (по умолчанию: support-analysis рядом с прогоном)")
    dash.add_argument("--output", help="Каталог результатов отчёта")
    dash.add_argument("--dashboard-dir", help="Каталог для dashboard.html (по умолчанию: каталог отчёта)")
    dash.add_argument("--service", help="Обработать только один сервис (vpn или hypertube); по умолчанию все")
    research = subparsers.add_parser("research", help="Единый прогон: бандл → ожидание оркестрации → merge → дашборд")
    research.add_argument("--run", help="Каталог прогона в data/ (по умолчанию: последний)")
    research.add_argument("--workers", type=int,
                          help="Число чанков субагентов, если бандл ещё не создан (2–20; по умолчанию: AGENT_WORKERS из .env или 10)")
    research.add_argument("--compare-with", help="Прошлый chatgpt_result.json для отчёта сравнения")
    research.add_argument("--dashboard-dir", help="Каталог для dashboard.html")
    research.add_argument("--service", help="Обработать только один сервис (vpn или hypertube); по умолчанию все")
    for cmd in (merge, research):
        cmd.add_argument("--no-notify", action="store_true",
                         help="Не отправлять дашборд в Slack после построения (по умолчанию отправляется, если заданы SLACK_BOT_TOKEN и SLACK_CHANNEL_ID)")
    notify = subparsers.add_parser("notify-slack", help="Отправить dashboard.html и статистику дашборда в Slack-канал")
    notify.add_argument("--run", help="Каталог прогона в data/ (по умолчанию: последний)")
    notify.add_argument("--service", help="Отправить дашборд только одного сервиса (vpn или hypertube); по умолчанию общий дашборд")
    notify.add_argument("--channel", help="ID канала Slack (по умолчанию: SLACK_CHANNEL_ID из .env)")
    return parser


def _print_other_review_pending(label: str, result: dict) -> None:
    print(
        f"{label}: нужна повторная проверка {result['review_count']} обращений из «Прочих»; "
        f"готовы результаты {result['review_ready']} из {result['review_workers']} проверяющих. "
        f"Манифест: {result['review_manifest_path']}. "
        "Запустите проверяющих с новым контекстом по agents_protocol.md из каталога проверки и повторите команду."
    )


def _maybe_notify_slack(args: argparse.Namespace, result: dict) -> None:
    """Автоотправка дашборда в Slack после merge-agents/research.

    Срабатывает, только если заданы SLACK_BOT_TOKEN и SLACK_CHANNEL_ID в .env
    и не передан --no-notify. Ошибка отправки не проваливает саму команду.
    """
    if getattr(args, "no_notify", False):
        return
    from . import config

    settings = config.load_slack_settings()
    if settings is None or not settings.channel_id:
        return
    import requests

    from .notify_slack import SlackNotifyError, send_dashboard

    try:
        file_id = send_dashboard(settings.bot_token, settings.channel_id,
                                 result["dashboard_path"])
    except (SlackNotifyError, OSError, requests.RequestException) as exc:
        print(f"Slack-уведомление не отправлено: {exc}")
        return
    print(f"Дашборд отправлен в Slack (канал {settings.channel_id}), file_id: {file_id}")


def _services_for_args(args: argparse.Namespace):
    """Список сервисов для обработки: один при --service, иначе все."""
    if getattr(args, "service", None):
        return [get_service(args.service)]
    return list(iter_services())


def _service_run_dir(service, explicit_run: str | None = None) -> Path:
    """Каталог прогона для сервиса."""
    if explicit_run:
        return Path(explicit_run)
    return find_latest_run(service_id=service.id)


def _build_combined_dashboard(service_dashboard_paths: dict[str, Path]) -> Path:
    """Собирает общий дашборд с вкладками из дашбордов сервисов.

    Пишет в общий родительский каталог прогонов сервисов:
    data/<stamp>/dashboard_DD_MM_YYYY.html для раскладки data/<stamp>/<service>,
    либо data/dashboard_DD_MM_YYYY.html для раскладки data/<service>/<stamp>.
    """
    from .dashboard import write_combined_dashboard

    services_data: dict[str, dict] = {}
    services_html: dict[str, str] = {}
    for sid, dash_path in service_dashboard_paths.items():
        json_path = dash_path.with_suffix(".json")
        services_data[sid] = json.loads(json_path.read_text(encoding="utf-8-sig"))
        services_html[sid] = dash_path.read_text(encoding="utf-8")
    run_roots = [dash_path.parent.parent for dash_path in service_dashboard_paths.values()]
    run_root = Path(run_roots[0]).resolve()
    if len(run_roots) > 1:
        run_root = Path(*run_root.parts[:len(os.path.commonprefix([list(r.resolve().parts) for r in run_roots]))])
    return write_combined_dashboard(services_data, services_html, run_root)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_analysis(args.input, args.output)
            size_mb = result["input_file_bytes"] / (1024 * 1024)
            print(f"Подготовлено обращений: {result['conversation_count']}; сообщений: {result['message_count']}; размер JSON: {size_mb:.1f} МБ. Файлы для ChatGPT: {result['output_dir']}")
            print("Входной JSON и PROMPT.md готовы для ручного анализа.")
        elif args.command == "report":
            result = generate_report(args.input, args.result, args.output)
            print(f"Отчёт сохранён: {result['report_path']}. Разметка: {result['classifications_path']}")
        elif args.command == "prepare-monitoring":
            result = prepare_monitoring_analysis(args.week, args.day, args.output, service_id=args.service)
            print(
                f"Подготовлены недельный срез ({result['week_conversation_count']}), дневной срез "
                f"({result['day_conversation_count']}), уникальных ID: {result['unique_conversation_count']}. "
                f"Один PROMPT.md и оба входных JSON: {result['output_dir']}"
            )
            if not args.no_agents:
                from .agents_orchestration import prepare_agents_bundle

                bundle = prepare_agents_bundle(
                    args.week, args.day, result["output_dir"],
                    workers=args.workers if args.workers is not None else load_agent_workers(),
                    service_id=getattr(args, "service", None),
                )
                print(
                    f"Пакет оркестрации субагентов готов: {bundle['workers']} чанков, "
                    f"таксономия: {bundle['taxonomy_source']}. Манифест: {bundle['work_dir']}/manifest.json. "
                    "Запустите субагентов по протоколу agents_protocol.md, затем: merge-agents --week ... --day ..."
                )
            print("JSON-файлы и PROMPT.md готовы для ручного анализа.")
        elif args.command == "merge-agents":
            from .agents_orchestration import get_billing_category, get_taxonomy, merge_agents_results

            explicit_service = bool(getattr(args, "service", None))
            services = _services_for_args(args)
            service_results: dict[str, dict] = {}
            pending_services: list[str] = []
            for service in services:
                args.service = service.id
                try:
                    week_path, day_path = _resolve_run_paths(args)
                except AnalysisError as exc:
                    if explicit_service:
                        raise
                    print(f"{service.label}: {exc}")
                    continue
                output = Path(args.output) if args.output else _default_monitoring_output(week_path, day_path)
                taxonomy = get_taxonomy(service.id)
                if not taxonomy:
                    print(f"{service.label}: таксономия не задана — слияние пропущено.")
                    continue
                recommendations = args.recommendations
                if recommendations is None:
                    default_rec = output / "agents_work" / "recommendations.json"
                    if default_rec.is_file():
                        recommendations = str(default_rec)
                manifest_path = output / "agents_work" / "manifest.json"
                taxonomy_source = "static_v5"
                if manifest_path.is_file():
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                    taxonomy_source = manifest.get("taxonomy_source", taxonomy_source)
                result = merge_agents_results(
                    week_path, day_path, output,
                    compare_with=args.compare_with,
                    recommendations=recommendations,
                    dashboard_dir=args.dashboard_dir,
                    taxonomy=taxonomy,
                    taxonomy_source=taxonomy_source,
                    billing_category=get_billing_category(service.id),
                    service_id=service.id,
                )
                if result.get("review_required"):
                    _print_other_review_pending(service.label, result)
                    pending_services.append(service.id)
                    continue
                print(
                    f"{service.label}: слито {result['unique_conversation_count']} классификаций. "
                    f"Отчёт: {result['report_path']}. Дашборд: {result['dashboard_path']}"
                )
                if "comparison_path" in result:
                    print(f"  Сравнение с предыдущим прогоном: {result['comparison_path']}")
                service_results[service.id] = result
            if pending_services:
                return 3
            if len(service_results) > 1:
                dashboards = {sid: Path(res["dashboard_path"]) for sid, res in service_results.items()}
                combined_dashboard = _build_combined_dashboard(dashboards)
                print(f"Общий дашборд: {combined_dashboard}")
                _maybe_notify_slack(args, {"dashboard_path": str(combined_dashboard)})
            elif service_results:
                _maybe_notify_slack(args, next(iter(service_results.values())))
            return 0
        elif args.command == "render-dashboard":
            week_path, day_path = _resolve_run_paths(args)
            result_path = Path(args.result) if args.result else _default_monitoring_output(week_path, day_path) / "chatgpt_result.json"
            outcome = render_dashboard(week_path, day_path, result_path,
                                       output_dir=args.output, dashboard_dir=args.dashboard_dir)
            print(f"Дашборд: {outcome['dashboard_path']}")
        elif args.command == "research":
            from .agents_orchestration import (
                get_billing_category, get_taxonomy, merge_agents_results, prepare_agents_bundle,
            )

            services = _services_for_args(args)
            service_results: dict[str, dict] = {}
            pending_services: list[str] = []
            run_roots: list[Path] = []
            for service in services:
                try:
                    run_dir = _service_run_dir(service, args.run)
                except AnalysisError as exc:
                    if args.service:
                        raise
                    print(f"{service.label}: {exc}")
                    continue
                run_roots.append(run_dir)
                week_path = run_dir / "last_week" / "conversations.json"
                day_path = run_dir / "last_day" / "conversations.json"
                output = run_dir / "support-analysis"
                taxonomy = get_taxonomy(service.id)
                if not taxonomy:
                    print(
                        f"{service.label}: таксономия не задана — классификация пропущена. "
                        f"Входные файлы для анализа: {output}"
                    )
                    continue
                if not (output / "agents_work" / "manifest.json").is_file():
                    bundle = prepare_agents_bundle(
                        week_path, day_path, output,
                        workers=args.workers if args.workers is not None else load_agent_workers(),
                        taxonomy=taxonomy,
                        taxonomy_source=f"{service.id}_v1",
                        service_id=service.id,
                    )
                    print(
                        f"{service.label}: пакет оркестрации субагентов готов: {bundle['workers']} чанков. "
                        f"Манифест: {bundle['work_dir']}/manifest.json"
                    )
                manifest = json.loads((output / "agents_work" / "manifest.json").read_text(encoding="utf-8-sig"))
                total = len(manifest["chunks"])
                ready = sum(1 for entry in manifest["chunks"] if Path(entry["result_path"]).is_file())
                if ready < total:
                    print(
                        f"{service.label}: Оркестрация не завершена: готовы результаты {ready} из {total} воркеров. "
                        "Запустите субагентов по протоколу agents_protocol.md и повторите research."
                    )
                    pending_services.append(service.id)
                    continue
                recommendations = output / "agents_work" / "recommendations.json"
                result = merge_agents_results(
                    week_path, day_path, output,
                    compare_with=args.compare_with,
                    recommendations=str(recommendations) if recommendations.is_file() else None,
                    dashboard_dir=args.dashboard_dir,
                    taxonomy=taxonomy,
                    taxonomy_source=manifest.get("taxonomy_source", f"{service.id}_v1"),
                    billing_category=get_billing_category(service.id),
                    service_id=service.id,
                )
                if result.get("review_required"):
                    _print_other_review_pending(service.label, result)
                    pending_services.append(service.id)
                    continue
                print(
                    f"{service.label}: слито {result['unique_conversation_count']} классификаций. "
                    f"Отчёт: {result['report_path']}. Дашборд: {result['dashboard_path']}"
                )
                if "comparison_path" in result:
                    print(f"  Сравнение с предыдущим прогоном: {result['comparison_path']}")
                service_results[service.id] = result
            if pending_services:
                return 3
            if len(service_results) > 1:
                dashboards = {sid: Path(res["dashboard_path"]) for sid, res in service_results.items()}
                combined_dashboard = _build_combined_dashboard(dashboards)
                print(f"Общий дашборд: {combined_dashboard}")
                _maybe_notify_slack(args, {"dashboard_path": str(combined_dashboard)})
            elif service_results:
                _maybe_notify_slack(args, next(iter(service_results.values())))
            return 0
        elif args.command == "notify-slack":
            from . import config
            from .notify_slack import send_dashboard

            settings = config.load_slack_settings()
            channel = args.channel or (settings.channel_id if settings else "")
            if settings is None or not channel:
                parser.error("notify-slack: задайте SLACK_BOT_TOKEN и SLACK_CHANNEL_ID в .env (или передайте --channel)")
            if args.service:
                service = get_service(args.service)
                run_dir = _service_run_dir(service, args.run)
                analysis = run_dir / "support-analysis"
                dashboards = sorted(analysis.glob("dashboard_*.html"))
                if not dashboards:
                    raise AnalysisError(f"Дашборд не найден: {analysis}/dashboard_DD_MM_YYYY.html")
                dashboard_path = dashboards[-1]
            else:
                services = list(iter_services())
                service_dashboards: dict[str, Path] = {}
                for service in services:
                    try:
                        run_dir = _service_run_dir(service, args.run)
                    except AnalysisError:
                        continue
                    analysis = run_dir / "support-analysis"
                    dashboards = sorted(analysis.glob("dashboard_*.html"))
                    if dashboards:
                        service_dashboards[service.id] = dashboards[-1]
                if len(service_dashboards) > 1:
                    dashboard_path = _build_combined_dashboard(service_dashboards)
                elif service_dashboards:
                    dashboard_path = next(iter(service_dashboards.values()))
                else:
                    raise AnalysisError("Дашборды не найдены ни для одного сервиса")
            file_id = send_dashboard(settings.bot_token, channel, dashboard_path)
            print(f"Дашборд отправлен в Slack (канал {channel}), file_id: {file_id}")
        else:
            result = generate_monitoring_report(args.week, args.day, args.result, args.output, args.dashboard_dir)
            print(
                f"Единый отчёт сохранён: {result['report_path']}. Разметка: {result['classifications_path']}. "
                f"Дашборд: {result['dashboard_path']}"
            )
    except AnalysisError as exc:
        print(f"Ошибка анализа: {exc}")
        return 1
    return 0

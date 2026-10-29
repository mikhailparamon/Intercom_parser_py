import argparse
import logging
import sys
from pathlib import Path

from .client import IntercomAPIError, IntercomClient
from .config import load_config
from .dates import DateRange, default_range, parse_date_range
from .exporter import ConversationExporter, format_duration


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("значение должно быть не меньше 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Выгрузка обращений и истории переписок из Intercom в JSONL")
    parser.add_argument("--start", help="Начало периода, YYYY-MM-DD (UTC)")
    parser.add_argument("--end", help="Конец периода, YYYY-MM-DD, не включая эту дату (UTC)")
    parser.add_argument("--output", default="data", help="Корневая директория результатов (по умолчанию: data)")
    parser.add_argument("--workers", type=positive_int, default=4, help="Максимум одновременных запросов деталей (по умолчанию: 4)")
    parser.add_argument("--selection", choices=("new", "updated", "all"), default="all",
                        help="Тип обращений: новые, обновлённые старые или все (по умолчанию: all)")
    parser.add_argument("--fields", choices=("important", "all"), default="important",
                        help="Объём полей: компактный или полный (по умолчанию: important)")
    parser.add_argument("--dry-run", action="store_true", help="Показать период и параметры без запросов к API")
    parser.add_argument("--verbose", action="store_true", help="Подробное логирование без содержимого переписок")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")
    try:
        if args.start is None and args.end is None:
            date_range = default_range()
        else:
            date_range = parse_date_range(args.start, args.end)
    except ValueError as exc:
        parser.error(str(exc))
    if args.dry_run:
        print(f"Период UTC: [{date_range.start}, {date_range.end}); отбор: {args.selection}; поля: {args.fields}; каталог: {Path(args.output) / f'{date_range.start}_{date_range.end}'}")
        return 0
    try:
        config = load_config()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    client = IntercomClient(config.access_token, config.api_version)
    try:
        result = ConversationExporter(client, max_workers=args.workers).run(
            date_range, args.output, api_version=config.api_version,
            selection=args.selection, fields=args.fields,
        )
    except IntercomAPIError as exc:
        logging.error("Экспорт не выполнен: %s", exc)
        return 1
    print(f"Экспорт завершён: {result['status']}. Найдено: {result['found_count']}, успешно обработано: {result['exported_count']}, ошибок: {result['error_count']}. Время: {format_duration(result['elapsed_seconds'])}. Каталог: {result['output_dir']}")
    return 0 if result["status"] == "success" else 1

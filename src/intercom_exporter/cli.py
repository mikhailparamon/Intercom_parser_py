import argparse
import logging
import sys
from pathlib import Path

from .client import IntercomAPIError, IntercomClient
from .config import load_config
from .dates import DateRange, default_range, parse_date_range
from .exporter import ConversationExporter, format_duration, resolve_timezone
from .support_analysis import AnalysisError, prepare_analysis


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("значение должно быть не меньше 1")
    return parsed


def valid_timezone(value: str) -> str:
    try:
        resolve_timezone(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if value.casefold() == "unix":
        return "unix"
    if value.casefold() == "utc":
        return "UTC"
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Выгрузка обращений и истории переписок из Intercom в JSON")
    parser.add_argument("--start", help="Начало периода, YYYY-MM-DD (UTC)")
    parser.add_argument("--end", help="Конец периода, YYYY-MM-DD, не включая эту дату (UTC)")
    parser.add_argument("--output", default="data", help="Корневая директория результатов (по умолчанию: data)")
    parser.add_argument("--workers", type=positive_int, default=15, help="Максимум одновременных запросов деталей (по умолчанию: 15)")
    parser.add_argument("--selection", choices=("new", "updated", "all"), default="all",
                        help="Тип обращений: новые, обновлённые старые или все (по умолчанию: all)")
    parser.add_argument("--fields", choices=("important", "all"), default="important",
                        help="Объём полей: компактный или полный (по умолчанию: important)")
    parser.add_argument("--timezone", type=valid_timezone, default="UTC",
                        help="Часовой пояс временных полей: UTC, IANA (например Europe/Moscow) или unix")
    parser.add_argument("--dry-run", action="store_true", help="Показать период и базовое имя каталога без запросов к API")
    parser.add_argument("--skip-analysis", action="store_true",
                        help="Не подготавливать файлы для ChatGPT после успешного экспорта")
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
        base_output_dir = Path(args.output) / f"{date_range.start}_{date_range.end}"
        print(f"Период UTC: [{date_range.start}, {date_range.end}); отбор: {args.selection}; поля: {args.fields}; время: {args.timezone}; базовый каталог: {base_output_dir} (если он занят, будет создан каталог с суффиксом)")
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
            selection=args.selection, fields=args.fields, timezone_name=args.timezone,
        )
    except IntercomAPIError as exc:
        logging.error("Экспорт не выполнен: %s", exc)
        return 1
    print(f"Экспорт завершён: {result['status']}. Найдено: {result['found_count']}, успешно обработано: {result['exported_count']}, ошибок: {result['error_count']}. Время: {format_duration(result['elapsed_seconds'])}. Каталог: {result['output_dir']}")
    if not args.skip_analysis and result["exported_count"] > 0:
        if result["status"] != "success":
            print(f"Предупреждение: экспорт частичный; анализ будет подготовлен по {result['exported_count']} выгруженным обращениям.")
        input_path = Path(result["output_dir"]) / "conversations.json"
        print("Подготовка данных для ChatGPT...")
        try:
            analysis = prepare_analysis(input_path)
        except AnalysisError as exc:
            logging.error("Не удалось подготовить данные для ChatGPT: %s", exc)
            return 1
        print(f"Данные подготовлены. Обращений: {analysis['conversation_count']}. Файлы: {analysis['output_dir']}")
    elif args.skip_analysis:
        print("Подготовка данных для ChatGPT отключена параметром --skip-analysis.")
    else:
        print("Подготовка данных для ChatGPT пропущена: нет выгруженных обращений.")
    return 0 if result["status"] == "success" else 1

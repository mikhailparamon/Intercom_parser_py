import argparse
import logging
import sys
from pathlib import Path

from .client import IntercomAPIError, IntercomClient
from .config import load_agent_workers, load_config
from .dates import default_range, parse_date_range
from .exporter import ConversationExporter, format_duration, resolve_timezone
from .monitoring import build_monitoring_windows, run_monitoring
from .support_analysis import AnalysisError, prepare_analysis, prepare_monitoring_analysis


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
    parser = argparse.ArgumentParser(description="Мониторинг обращений Intercom и их экспорт в JSON")
    parser.add_argument("--standard", action="store_true",
                        help="Использовать обычный экспорт вместо режима мониторинга по умолчанию")
    parser.add_argument("--full-archive", action="store_true",
                        help="Мониторинг: выгружать архив за последние 3 месяца вместо недели")
    parser.add_argument("--start", help="Начало периода, YYYY-MM-DD (UTC)")
    parser.add_argument("--end", help="Конец периода, YYYY-MM-DD, не включая эту дату (UTC)")
    parser.add_argument("--output", default="data", help="Корневая директория результатов (по умолчанию: data)")
    parser.add_argument("--workers", type=positive_int, default=15, help="Максимум одновременных запросов деталей (по умолчанию: 15)")
    parser.add_argument("--selection", choices=("new", "updated", "all"),
                        help="Тип обращений: новые, обновлённые старые или все (по умолчанию: all)")
    parser.add_argument("--fields", choices=("important", "all"),
                        help="Объём полей: компактный или полный (по умолчанию: important)")
    parser.add_argument("--timezone", type=valid_timezone, default="UTC",
                        help="Часовой пояс временных полей: UTC, IANA (например Europe/Moscow) или unix")
    parser.add_argument("--dry-run", action="store_true", help="Показать период и базовое имя каталога без запросов к API")
    parser.add_argument("--skip-analysis", action="store_true",
                        help="Отключить подготовку файлов для анализа после экспорта")
    parser.add_argument("--no-agents", action="store_true",
                        help="Мониторинг: не создавать пакет оркестрации субагентов после выгрузки")
    parser.add_argument("--agent-workers", type=int,
                        help="Число чанков субагентов в пакете оркестрации (2–20; по умолчанию: AGENT_WORKERS из .env или 10)")
    parser.add_argument("--verbose", action="store_true", help="Подробное логирование без содержимого переписок")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")
    if args.standard:
        if args.full_archive:
            parser.error("Параметр --full-archive доступен только в режиме мониторинга")
        try:
            if args.start is None and args.end is None:
                date_range = default_range()
            else:
                date_range = parse_date_range(args.start, args.end)
        except ValueError as exc:
            parser.error(str(exc))
        selection = args.selection or "all"
        fields = args.fields or "important"
        if args.dry_run:
            base_output_dir = Path(args.output) / date_range.slug
            print(f"Обычный экспорт UTC: [{date_range.start}, {date_range.end}); отбор: {selection}; поля: {fields}; время: {args.timezone}; базовый каталог: {base_output_dir} (если он занят, будет создан каталог с суффиксом)")
            return 0
    else:
        if args.start is not None or args.end is not None:
            parser.error("Параметры --start и --end доступны с --standard")
        if args.selection is not None or args.fields is not None:
            parser.error("Параметры --selection и --fields доступны с --standard; мониторинг всегда использует all")
        windows = build_monitoring_windows()
        if args.dry_run:
            source = windows["backup_3_months"] if args.full_archive else windows["last_week"]
            base_output_dir = Path(args.output) / source.slug
            if args.full_archive:
                archive_line = f"архив [{windows['backup_3_months'].start.isoformat()}, {windows['backup_3_months'].end.isoformat()}); "
            else:
                archive_line = "архив выключен (включить: --full-archive); "
            print(
                "Мониторинг UTC: " + archive_line +
                f"неделя [{windows['last_week'].start.isoformat()}, {windows['last_week'].end.isoformat()}); "
                f"день [{windows['last_day'].start.isoformat()}, {windows['last_day'].end.isoformat()}); "
                f"поля: all; базовый каталог: {base_output_dir} (при повторном запуске будет создан новый каталог)"
            )
            return 0
    try:
        config = load_config()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    client = IntercomClient(config.access_token, config.api_version)
    exporter = ConversationExporter(client, max_workers=args.workers)
    if not args.standard:
        try:
            result = run_monitoring(
                exporter, args.output, full_archive=args.full_archive, windows=windows, api_version=config.api_version,
                timezone_name=args.timezone,
                prepare=None if args.skip_analysis else prepare_monitoring_analysis,
                agents=not args.no_agents,
                agent_workers=args.agent_workers if args.agent_workers is not None else load_agent_workers(),
            )
        except (IntercomAPIError, AnalysisError, ValueError) as exc:
            logging.error("Мониторинг не выполнен: %s", exc)
            return 1
        export_result = result["export"]
        print(
            f"Экспорт завершён: {export_result['status']}. "
            f"Найдено: {export_result['found_count']}, успешно обработано: "
            f"{export_result['exported_count']}, ошибок: {export_result['error_count']}. "
            f"Время: {format_duration(export_result['elapsed_seconds'])}."
        )
        labels = [("last_week", "Срез за неделю"), ("last_day", "Срез за день")]
        if args.full_archive:
            labels.insert(0, ("backup_3_months", "Архив за 3 месяца"))
        for name, label in labels:
            snapshot = result["snapshots"][name]
            print(f"{label}: {snapshot['conversation_count']} обращений. Каталог: {snapshot['output_dir']}")
        if result["analysis"]:
            print(f"Общие файлы для анализа обоих периодов: {result['analysis']['output_dir']}")
            if result["analysis"].get("agents_bundle"):
                bundle = result["analysis"]["agents_bundle"]
                print(
                    f"Пакет оркестрации субагентов готов: {bundle['workers']} чанков, "
                    f"таксономия: {bundle['taxonomy_source']}. Манифест: {bundle['work_dir']}/manifest.json."
                )
            print("Подготовлены файлы для ручного анализа в ChatGPT.")
        if args.skip_analysis:
            print("Подготовка данных для ChatGPT отключена параметром --skip-analysis.")
        return 0 if export_result["status"] == "success" else 1

    try:
        result = exporter.run(
            date_range, args.output, api_version=config.api_version,
            selection=selection, fields=fields, timezone_name=args.timezone,
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
        print("Подготовлены файлы для ручного анализа в ChatGPT.")
    elif args.skip_analysis:
        print("Подготовка данных для ChatGPT отключена параметром --skip-analysis.")
    else:
        print("Подготовка данных для ChatGPT пропущена: нет выгруженных обращений.")
    return 0 if result["status"] == "success" else 1

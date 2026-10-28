from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self):
        if self.end <= self.start:
            raise ValueError("Дата окончания должна быть позже даты начала")

    def epoch_bounds(self) -> tuple[int, int]:
        start_dt = datetime.combine(self.start, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(self.end, time.min, tzinfo=timezone.utc)
        return int(start_dt.timestamp()), int(end_dt.timestamp())

    def contains(self, epoch_seconds: int) -> bool:
        start, end = self.epoch_bounds()
        return start <= int(epoch_seconds) < end


def parse_date_range(start: str | None, end: str | None) -> DateRange:
    if (start is None) != (end is None):
        raise ValueError("Нужно указать обе даты: --start и --end")
    if start is None:
        raise ValueError("Даты периода не заданы")
    try:
        parsed_start = date.fromisoformat(start)
        parsed_end = date.fromisoformat(end)
    except (TypeError, ValueError) as exc:
        raise ValueError("Дата должна быть в формате YYYY-MM-DD") from exc
    if parsed_start.isoformat() != start or parsed_end.isoformat() != end:
        raise ValueError("Дата должна быть в формате YYYY-MM-DD")
    return DateRange(parsed_start, parsed_end)


def default_range(now: datetime | None = None) -> DateRange:
    current = now or datetime.now(timezone.utc)
    current = current.astimezone(timezone.utc) if current.tzinfo else current.replace(tzinfo=timezone.utc)
    end = date(current.year, current.month, 1)
    month_index = end.year * 12 + end.month - 1 - 3
    start = date(month_index // 12, month_index % 12 + 1, 1)
    return DateRange(start, end)

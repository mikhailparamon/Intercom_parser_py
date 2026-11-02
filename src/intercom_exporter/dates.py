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

    @property
    def slug(self) -> str:
        return f"{self.start.isoformat()}_{self.end.isoformat()}"


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class DateTimeRange:
    """Скользящий период с точностью до секунды; границы хранятся в UTC."""

    start: datetime
    end: datetime

    def __post_init__(self):
        start = _ensure_utc(self.start)
        end = _ensure_utc(self.end)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        if end <= start:
            raise ValueError("Момент окончания должен быть позже момента начала")

    def epoch_bounds(self) -> tuple[int, int]:
        return int(self.start.timestamp()), int(self.end.timestamp())

    def contains(self, epoch_seconds: int) -> bool:
        start, end = self.epoch_bounds()
        return start <= int(epoch_seconds) < end

    @property
    def slug(self) -> str:
        stamp = "%Y%m%dT%H%M%SZ"
        return f"{self.start.strftime(stamp)}_{self.end.strftime(stamp)}"


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


def default_range(now: datetime | None = None) -> DateTimeRange:
    """Скользящая неделя: ровно последние 7 * 24 часа от момента запуска (UTC)."""
    current = now or datetime.now(timezone.utc)
    return DateTimeRange(current - timedelta(days=7), current)

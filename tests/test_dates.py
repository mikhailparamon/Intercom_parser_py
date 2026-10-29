from datetime import date, datetime, timedelta, timezone

import pytest

from intercom_exporter.dates import DateRange, default_range, parse_date_range


def test_default_range_is_last_three_calendar_months_through_today():
    result = default_range(datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    assert result == DateRange(date(2026, 7, 29), date(2026, 10, 30))


def test_explicit_dates_use_inclusive_start_and_exclusive_end():
    assert parse_date_range("2026-07-01", "2026-10-01") == DateRange(date(2026, 7, 1), date(2026, 10, 1))


@pytest.mark.parametrize("start,end", [("2026-02-30", "2026-03-01"), ("01-01-2026", "2026-02-01")])
def test_invalid_date_format_is_rejected(start, end):
    with pytest.raises(ValueError):
        parse_date_range(start, end)


def test_one_missing_date_is_rejected():
    with pytest.raises(ValueError, match="--start"):
        parse_date_range("2026-01-01", None)


def test_end_must_be_later_than_start():
    with pytest.raises(ValueError, match="позже"):
        parse_date_range("2026-01-01", "2026-01-01")


@pytest.mark.parametrize(
    "today,expected_start",
    [
        (datetime(2026, 5, 31, tzinfo=timezone.utc), date(2026, 2, 28)),
        (datetime(2024, 5, 31, tzinfo=timezone.utc), date(2024, 2, 29)),
        (datetime(2026, 3, 31, tzinfo=timezone.utc), date(2025, 12, 31)),
    ],
)
def test_default_range_clamps_day_when_target_month_is_short(today, expected_start):
    result = default_range(today)
    assert result == DateRange(expected_start, today.date() + timedelta(days=1))

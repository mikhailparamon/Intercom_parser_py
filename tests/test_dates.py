from datetime import date, datetime, timezone

import pytest

from intercom_exporter.dates import DateRange, default_range, parse_date_range


def test_default_range_is_previous_three_full_calendar_months():
    result = default_range(datetime(2026, 10, 28, 12, tzinfo=timezone.utc))
    assert result == DateRange(date(2026, 7, 1), date(2026, 10, 1))


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


def test_month_subtraction_handles_short_months():
    result = default_range(datetime(2026, 5, 10, tzinfo=timezone.utc))
    assert result == DateRange(date(2026, 2, 1), date(2026, 5, 1))

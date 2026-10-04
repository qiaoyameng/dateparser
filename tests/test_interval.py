from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from dateparser.date import DateDataParser
from dateparser.interval import (
    PART_SOURCE_INPUT,
    PART_SOURCE_PREFERENCE,
    PART_SOURCE_RELATIVE_BASE,
    DateInterval,
    interval_from_date_data,
)

RELATIVE_BASE = datetime(2026, 10, 4, 15, 30, 45, 123456)  # a Sunday


def get_interval(
    date_string: str,
    settings: dict[str, Any] | None = None,
    date_formats: list[str] | None = None,
) -> DateInterval:
    parser_settings: dict[str, Any] = {"RELATIVE_BASE": RELATIVE_BASE}
    if settings:
        parser_settings.update(settings)
    parser = DateDataParser(languages=["en"], settings=parser_settings)
    return parser.get_interval_data(date_string, date_formats)


@pytest.mark.parametrize(
    ("date_string", "start", "end", "precision"),
    [
        ("2025", datetime(2025, 1, 1), datetime(2026, 1, 1), "year"),
        ("March 2025", datetime(2025, 3, 1), datetime(2025, 4, 1), "month"),
        ("2025-03-15", datetime(2025, 3, 15), datetime(2025, 3, 16), "day"),
        (
            "15 March 2025 4pm",
            datetime(2025, 3, 15, 16),
            datetime(2025, 3, 15, 17),
            "hour",
        ),
        (
            "2025-03-15 14:30",
            datetime(2025, 3, 15, 14, 30),
            datetime(2025, 3, 15, 14, 31),
            "minute",
        ),
        (
            "2025-03-15 14:30:45",
            datetime(2025, 3, 15, 14, 30, 45),
            datetime(2025, 3, 15, 14, 30, 46),
            "second",
        ),
        (
            "2025-03-15 14:30:45.123456",
            datetime(2025, 3, 15, 14, 30, 45, 123456),
            datetime(2025, 3, 15, 14, 30, 45, 123457),
            "microsecond",
        ),
    ],
)
def test_interval_boundaries_and_precision(
    date_string: str, start: datetime, end: datetime, precision: str
) -> None:
    interval = get_interval(date_string)
    assert interval.start == start
    assert interval.end == end
    assert interval.precision == precision
    assert interval.reason is None


@pytest.mark.parametrize(
    ("date_string", "precision"),
    [
        ("1570308760", "second"),
        ("1570308760263", "millisecond"),
        ("1570308760263111", "microsecond"),
    ],
)
def test_timestamp_resolution_is_preserved(date_string: str, precision: str) -> None:
    interval = get_interval(date_string)
    assert interval.precision == precision
    assert interval.end is not None
    assert interval.start is not None
    spans = {"second": 10**6, "millisecond": 10**3, "microsecond": 1}
    assert (interval.end - interval.start) == timedelta(microseconds=spans[precision])


@pytest.mark.parametrize(
    ("date_string", "days"),
    [
        ("February 2024", 29),  # leap year
        ("February 2023", 28),
        ("March 2025", 31),
        ("April 2025", 30),
    ],
)
def test_month_interval_uses_calendar_month(date_string: str, days: int) -> None:
    interval = get_interval(date_string)
    assert interval.precision == "month"
    assert interval.end is not None and interval.start is not None
    assert (interval.end - interval.start) == timedelta(days=days)


def test_leap_year_february_boundaries() -> None:
    interval = get_interval("February 2024")
    assert interval.start == datetime(2024, 2, 1)
    assert interval.end == datetime(2024, 3, 1)

    day = get_interval("2024-02-29")
    assert day.precision == "day"
    assert day.start == datetime(2024, 2, 29)
    assert day.end == datetime(2024, 3, 1)


def test_year_interval_uses_calendar_year() -> None:
    interval = get_interval("2024")
    assert interval.precision == "year"
    assert interval.end is not None and interval.start is not None
    assert (interval.end - interval.start) == timedelta(days=366)  # leap year


@pytest.mark.parametrize(
    ("date_string", "hours"),
    [
        ("2021-11-07", 25),  # DST fall-back day in US/Eastern
        ("2021-03-14", 23),  # DST spring-forward day in US/Eastern
        ("2021-11-08", 24),
    ],
)
def test_aware_day_crossing_dst_is_not_24_hours(
    date_string: str, hours: int
) -> None:
    interval = get_interval(
        date_string,
        settings={"TIMEZONE": "US/Eastern", "RETURN_AS_TIMEZONE_AWARE": True},
    )
    assert interval.precision == "day"
    assert interval.end is not None and interval.start is not None
    # Bounds are local midnights...
    assert (interval.start.hour, interval.start.minute) == (0, 0)
    assert (interval.end.hour, interval.end.minute) == (0, 0)
    parsed_date = datetime.strptime(date_string, "%Y-%m-%d").date()
    assert interval.start.date() == parsed_date
    assert interval.end.date() == parsed_date + timedelta(days=1)
    # ...so the absolute span follows the DST transition, not 24 hours.
    span = interval.end.astimezone(timezone.utc) - interval.start.astimezone(
        timezone.utc
    )
    assert span == timedelta(hours=hours)


@pytest.mark.parametrize(
    ("date_string", "settings", "expected"),
    [
        (
            "March 2025",
            None,
            {
                "year": PART_SOURCE_INPUT,
                "month": PART_SOURCE_INPUT,
                "day": PART_SOURCE_RELATIVE_BASE,
            },
        ),
        (
            "March 2025",
            {"PREFER_DAY_OF_MONTH": "last"},
            {
                "year": PART_SOURCE_INPUT,
                "month": PART_SOURCE_INPUT,
                "day": PART_SOURCE_PREFERENCE,
            },
        ),
        (
            "2025",
            {"PREFER_MONTH_OF_YEAR": "first"},
            {
                "year": PART_SOURCE_INPUT,
                "month": PART_SOURCE_PREFERENCE,
                "day": PART_SOURCE_RELATIVE_BASE,
            },
        ),
        (
            "2025-03-15 14:30",
            None,
            {
                "year": PART_SOURCE_INPUT,
                "month": PART_SOURCE_INPUT,
                "day": PART_SOURCE_INPUT,
                "hour": PART_SOURCE_INPUT,
                "minute": PART_SOURCE_INPUT,
            },
        ),
        (
            "4pm",
            None,
            {
                "year": PART_SOURCE_RELATIVE_BASE,
                "month": PART_SOURCE_RELATIVE_BASE,
                "day": PART_SOURCE_RELATIVE_BASE,
                "hour": PART_SOURCE_INPUT,
            },
        ),
    ],
)
def test_part_sources(
    date_string: str, settings: dict[str, Any] | None, expected: dict[str, str]
) -> None:
    interval = get_interval(date_string, settings=settings)
    assert interval.part_sources == expected


def test_part_sources_with_date_formats() -> None:
    interval = get_interval("22/05", date_formats=["%d/%m"])
    assert interval.precision == "day"
    assert interval.part_sources == {
        "year": PART_SOURCE_RELATIVE_BASE,
        "month": PART_SOURCE_INPUT,
        "day": PART_SOURCE_INPUT,
    }
    assert interval.base == RELATIVE_BASE


def test_weekday_only_documents_base_and_meaning() -> None:
    interval = get_interval("Monday")
    assert interval.precision == "day"
    assert interval.start == datetime(2026, 9, 28)  # Monday before the base
    assert interval.end == datetime(2026, 9, 29)
    assert interval.part_sources == {
        "year": PART_SOURCE_RELATIVE_BASE,
        "month": PART_SOURCE_RELATIVE_BASE,
        "day": PART_SOURCE_INPUT,
    }
    assert interval.base == RELATIVE_BASE
    assert interval.meaning is not None
    assert "weekday" in interval.meaning
    assert RELATIVE_BASE.isoformat() in interval.meaning


@pytest.mark.parametrize(
    ("date_string", "start", "end", "precision"),
    [
        ("2 hours ago", datetime(2026, 10, 4, 13), datetime(2026, 10, 4, 14), "hour"),
        (
            "30 minutes ago",
            datetime(2026, 10, 4, 15),
            datetime(2026, 10, 4, 15, 1),
            "minute",
        ),
        ("yesterday", datetime(2026, 10, 3), datetime(2026, 10, 4), "day"),
        ("2 years ago", datetime(2024, 1, 1), datetime(2025, 1, 1), "year"),
        ("3 months ago", datetime(2026, 7, 1), datetime(2026, 8, 1), "month"),
    ],
)
def test_relative_expressions(
    date_string: str, start: datetime, end: datetime, precision: str
) -> None:
    interval = get_interval(date_string)
    assert interval.start == start
    assert interval.end == end
    assert interval.precision == precision
    assert interval.base is not None
    assert interval.base.replace(tzinfo=None) == RELATIVE_BASE
    assert interval.meaning is not None
    assert "relative expression" in interval.meaning
    assert precision in interval.meaning
    assert set(interval.part_sources.values()) == {PART_SOURCE_RELATIVE_BASE}


def test_relative_expression_with_explicit_day() -> None:
    interval = get_interval("the 1st of last month")
    assert interval.precision == "day"
    assert interval.start == datetime(2026, 9, 1)
    assert interval.end == datetime(2026, 9, 2)
    assert interval.part_sources["day"] == PART_SOURCE_INPUT
    assert interval.part_sources["month"] == PART_SOURCE_RELATIVE_BASE
    assert interval.meaning is not None
    assert "relative expression" in interval.meaning


def test_unparseable_input_reports_reason() -> None:
    interval = get_interval("foo")
    assert interval.start is None
    assert interval.end is None
    assert interval.precision is None
    assert interval.reason is not None
    assert "could be parsed" in interval.reason


def test_overflowing_interval_end_reports_reason() -> None:
    interval = get_interval("9999-12-31")
    assert interval.start == datetime(9999, 12, 31)
    assert interval.end is None
    assert interval.precision == "day"
    assert interval.reason is not None
    assert "overflow" in interval.reason


def test_interval_mode_does_not_change_date_data() -> None:
    parser = DateDataParser(
        languages=["en"], settings={"RELATIVE_BASE": RELATIVE_BASE}
    )
    date_data = parser.get_date_data("March 2025")
    assert date_data.date_obj == datetime(2025, 3, 4)
    assert date_data.period == "month"
    assert date_data.parts == ("year", "month")
    assert parser.get_date_tuple("March 2025") == (
        datetime(2025, 3, 4),
        "month",
        "en",
    )


def test_date_interval_supports_dict_access() -> None:
    interval = get_interval("March 2025")
    assert interval["precision"] == "month"
    assert interval["start"] == datetime(2025, 3, 1)
    with pytest.raises(KeyError):
        interval["nope"]


def test_interval_from_date_data_without_settings() -> None:
    date_data = DateDataParser(languages=["en"]).get_date_data("March 2025")
    interval = interval_from_date_data(date_data)
    assert interval.start == datetime(2025, 3, 1)
    assert interval.end == datetime(2025, 4, 1)
    assert interval.precision == "month"

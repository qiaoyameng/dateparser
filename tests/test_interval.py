from datetime import datetime, timedelta

import pytest
from pytz import timezone

import dateparser
from dateparser import DateDataParser
from dateparser.interval import IntervalData

BASE = datetime(2025, 6, 16, 10, 30)  # a Monday
EASTERN = timezone("US/Eastern")


def get_interval(date_string, date_formats=None, **settings):
    parser = DateDataParser(settings=settings)
    return parser.get_interval_data(date_string, date_formats)


class TestIntervalBounds:
    @pytest.mark.parametrize(
        ("date_string", "start", "end", "precision"),
        [
            ("2024", datetime(2024, 1, 1), datetime(2025, 1, 1), "year"),
            (
                "March 2025",
                datetime(2025, 3, 1),
                datetime(2025, 4, 1),
                "month",
            ),
            (
                "December 2025",
                datetime(2025, 12, 1),
                datetime(2026, 1, 1),
                "month",
            ),
            (
                "2025-03-15",
                datetime(2025, 3, 15),
                datetime(2025, 3, 16),
                "day",
            ),
            (
                "2025-03-15 10:30",
                datetime(2025, 3, 15, 10, 30),
                datetime(2025, 3, 15, 10, 31),
                "minute",
            ),
            (
                "2025-03-15 10:30:45",
                datetime(2025, 3, 15, 10, 30, 45),
                datetime(2025, 3, 15, 10, 30, 46),
                "second",
            ),
            (
                "2025-03-15 10:30:45.123456",
                datetime(2025, 3, 15, 10, 30, 45, 123456),
                datetime(2025, 3, 15, 10, 30, 45, 123457),
                "microsecond",
            ),
            (
                "10:30 pm",
                datetime(2025, 6, 16, 22, 30),
                datetime(2025, 6, 16, 22, 31),
                "minute",
            ),
        ],
    )
    def test_bounds_and_precision(self, date_string, start, end, precision):
        interval = get_interval(date_string, RELATIVE_BASE=BASE)
        assert interval.start == start
        assert interval.end == end
        assert interval.precision == precision
        assert interval.error is None

    def test_months_follow_the_calendar_not_a_fixed_length(self):
        interval = get_interval("February 2024", RELATIVE_BASE=BASE)
        assert interval.end - interval.start == timedelta(days=29)
        interval = get_interval("February 2023", RELATIVE_BASE=BASE)
        assert interval.end - interval.start == timedelta(days=28)
        interval = get_interval("April 2025", RELATIVE_BASE=BASE)
        assert interval.end - interval.start == timedelta(days=30)

    def test_leap_year_is_longer(self):
        interval = get_interval("2024", RELATIVE_BASE=BASE)
        assert interval.end - interval.start == timedelta(days=366)
        interval = get_interval("2023", RELATIVE_BASE=BASE)
        assert interval.end - interval.start == timedelta(days=365)

    def test_february_29_without_year_uses_a_leap_year(self):
        interval = get_interval("29 February", RELATIVE_BASE=datetime(2023, 5, 1))
        assert interval.start == datetime(2024, 2, 29)
        assert interval.end == datetime(2024, 3, 1)
        assert interval.precision == "day"

    def test_hour_precision(self):
        interval = get_interval("2025-03-15 10 pm", RELATIVE_BASE=BASE)
        assert interval.start == datetime(2025, 3, 15, 22, 0)
        assert interval.end == datetime(2025, 3, 15, 23, 0)
        assert interval.precision == "hour"

    def test_millisecond_fraction(self):
        interval = get_interval("2025-03-15 10:30:45.123", RELATIVE_BASE=BASE)
        assert interval.start == datetime(2025, 3, 15, 10, 30, 45, 123000)
        assert interval.end - interval.start == timedelta(milliseconds=1)
        assert interval.precision == "millisecond"


class TestTimestampIntervals:
    def test_seconds_timestamp(self):
        interval = get_interval("1577836800", TIMEZONE="UTC")
        assert interval.precision == "second"
        assert interval.end - interval.start == timedelta(seconds=1)

    def test_milliseconds_timestamp(self):
        interval = get_interval("1577836800123", TIMEZONE="UTC")
        assert interval.precision == "millisecond"
        assert interval.start == datetime(2020, 1, 1, 0, 0, 0, 123000)
        assert interval.end - interval.start == timedelta(milliseconds=1)

    def test_microseconds_timestamp(self):
        interval = get_interval("1577836800123456", TIMEZONE="UTC")
        assert interval.precision == "microsecond"
        assert interval.start == datetime(2020, 1, 1, 0, 0, 0, 123456)
        assert interval.end - interval.start == timedelta(microseconds=1)


class TestDaylightSavingTime:
    settings = {"TIMEZONE": "US/Eastern", "RETURN_AS_TIMEZONE_AWARE": True}

    def test_fall_back_day_is_25_hours(self):
        interval = get_interval("November 7, 2021", **self.settings)
        assert interval.start == EASTERN.localize(datetime(2021, 11, 7))
        assert interval.end == EASTERN.localize(datetime(2021, 11, 8))
        assert interval.end - interval.start == timedelta(hours=25)
        assert interval.precision == "day"
        assert interval.error is None

    def test_spring_forward_day_is_23_hours(self):
        interval = get_interval("March 14, 2021", **self.settings)
        assert interval.start == EASTERN.localize(datetime(2021, 3, 14))
        assert interval.end == EASTERN.localize(datetime(2021, 3, 15))
        assert interval.end - interval.start == timedelta(hours=23)
        assert interval.precision == "day"
        assert interval.error is None

    def test_ambiguous_boundary_reports_reason(self):
        # 01:30 occurs twice on the fall-back day.
        interval = get_interval("2021-11-07 01:30", **self.settings)
        assert interval.start is None
        assert interval.end is None
        assert interval.precision == "minute"
        assert "ambiguous" in interval.error

    def test_nonexistent_boundary_reports_reason(self):
        # 02:30 does not occur on the spring-forward day.
        interval = get_interval("2021-03-14 02:30", **self.settings)
        assert interval.start is None
        assert interval.end is None
        assert "does not exist" in interval.error


class TestPartSources:
    def test_input_and_relative_base_parts(self):
        interval = get_interval("March 2025", RELATIVE_BASE=BASE)
        assert interval.sources == {
            "year": "input",
            "month": "input",
            "day": "relative_base",
        }
        assert interval.relative_base == BASE

    def test_all_parts_from_input(self):
        interval = get_interval("2025-03-15 10:30", RELATIVE_BASE=BASE)
        assert interval.sources == {
            "year": "input",
            "month": "input",
            "day": "input",
            "time": "input",
        }

    def test_preference_filled_parts(self):
        interval = get_interval(
            "March 2025", RELATIVE_BASE=BASE, PREFER_DAY_OF_MONTH="last"
        )
        assert interval.sources["day"] == "preference"
        interval = get_interval(
            "2025", RELATIVE_BASE=BASE, PREFER_MONTH_OF_YEAR="first"
        )
        assert interval.sources["month"] == "preference"
        assert interval.sources["day"] == "relative_base"

    def test_time_only_date_parts_come_from_relative_base(self):
        interval = get_interval("10:30 pm", RELATIVE_BASE=BASE)
        assert interval.sources == {
            "year": "relative_base",
            "month": "relative_base",
            "day": "relative_base",
            "time": "input",
        }


class TestWeekdayAndRelativeBasis:
    def test_weekday_only_records_base_and_basis(self):
        interval = get_interval("Friday", RELATIVE_BASE=BASE)
        assert interval.basis == "weekday"
        assert interval.relative_base == BASE
        # The Friday before Monday 2025-06-16.
        assert interval.start == datetime(2025, 6, 13)
        assert interval.end == datetime(2025, 6, 14)
        assert interval.precision == "day"
        assert interval.sources["day"] == "relative_base"

    def test_weekday_with_prefer_dates_from(self):
        interval = get_interval(
            "Friday", RELATIVE_BASE=BASE, PREFER_DATES_FROM="future"
        )
        assert interval.basis == "weekday"
        assert interval.start == datetime(2025, 6, 20)
        assert interval.sources["day"] == "preference"

    def test_relative_expression_records_base_and_basis(self):
        interval = get_interval("3 days ago", RELATIVE_BASE=BASE)
        assert interval.basis == "relative"
        assert interval.relative_base is not None
        assert interval.relative_base.replace(tzinfo=None) == BASE
        assert interval.start == datetime(2025, 6, 13)
        assert interval.end == datetime(2025, 6, 14)
        assert interval.precision == "day"
        assert interval.sources == {
            "year": "relative_base",
            "month": "relative_base",
            "day": "relative_base",
        }

    @pytest.mark.parametrize(
        ("date_string", "start", "end", "precision"),
        [
            ("2 hours ago", datetime(2025, 6, 16, 8), datetime(2025, 6, 16, 9), "hour"),
            (
                "5 minutes ago",
                datetime(2025, 6, 16, 10, 25),
                datetime(2025, 6, 16, 10, 26),
                "minute",
            ),
            (
                "30 seconds ago",
                datetime(2025, 6, 16, 10, 29, 30),
                datetime(2025, 6, 16, 10, 29, 31),
                "second",
            ),
            ("2 months ago", datetime(2025, 4, 1), datetime(2025, 5, 1), "month"),
            ("1 year ago", datetime(2024, 1, 1), datetime(2025, 1, 1), "year"),
            ("2 weeks ago", datetime(2025, 6, 2), datetime(2025, 6, 9), "week"),
        ],
    )
    def test_relative_units(self, date_string, start, end, precision):
        interval = get_interval(date_string, RELATIVE_BASE=BASE)
        assert interval.start == start
        assert interval.end == end
        assert interval.precision == precision
        assert interval.basis == "relative"

    def test_relative_with_explicit_time(self):
        interval = get_interval("1 day ago, 2 PM", RELATIVE_BASE=BASE)
        assert interval.precision == "hour"
        assert interval.start == datetime(2025, 6, 15, 14)
        assert interval.sources["time"] == "input"
        assert interval.sources["day"] == "relative_base"


class TestIntervalErrors:
    def test_unparseable_string(self):
        interval = get_interval("not a date at all")
        assert interval.start is None
        assert interval.end is None
        assert interval.precision is None
        assert interval.error is not None

    def test_overflowing_end_reports_reason(self):
        interval = get_interval("9999", RELATIVE_BASE=BASE)
        assert interval.start == datetime(9999, 1, 1)
        assert interval.end is None
        assert interval.precision == "year"
        assert "overflow" in interval.error


class TestCustomFormats:
    def test_month_format(self):
        interval = get_interval("2025/03", date_formats=["%Y/%m"], RELATIVE_BASE=BASE)
        assert interval.start == datetime(2025, 3, 1)
        assert interval.end == datetime(2025, 4, 1)
        assert interval.precision == "month"

    def test_format_with_seconds(self):
        interval = get_interval(
            "2025-03-15 10:30:45", date_formats=["%Y-%m-%d %H:%M:%S"]
        )
        assert interval.precision == "second"
        assert interval.end - interval.start == timedelta(seconds=1)


class TestPartOfDay:
    def test_part_of_day_interval(self):
        interval = get_interval(
            "tonight", RELATIVE_BASE=BASE, RETURN_TIME_AS_PERIOD=True
        )
        assert interval.precision == "part_of_day"
        assert interval.start == datetime(2025, 6, 16, 20, 0)
        assert interval.end == datetime(2025, 6, 17, 3, 0)
        assert interval.sources["time"] == "preference"


class TestTopLevelAPI:
    def test_parse_interval(self):
        interval = dateparser.parse_interval("March 2025")
        assert isinstance(interval, IntervalData)
        assert interval.start == datetime(2025, 3, 1)
        assert interval.end == datetime(2025, 4, 1)
        assert interval.precision == "month"

    def test_parse_interval_with_settings(self):
        interval = dateparser.parse_interval(
            "3 days ago", settings={"RELATIVE_BASE": BASE}
        )
        assert interval.start == datetime(2025, 6, 13)
        assert interval.basis == "relative"

    def test_parse_interval_returns_none_when_unparseable(self):
        assert dateparser.parse_interval("not a date at all") is None

    def test_interval_data_dict_access(self):
        interval = dateparser.parse_interval("March 2025")
        assert interval["start"] == datetime(2025, 3, 1)
        assert interval["precision"] == "month"
        with pytest.raises(KeyError):
            interval["unknown"]


class TestDateDataUnchanged:
    @pytest.mark.parametrize(
        ("date_string", "expected"),
        [
            ("March 2025", datetime(2025, 3, 16)),
            ("2024", datetime(2024, 6, 16)),
            ("2025-03-15 10:30", datetime(2025, 3, 15, 10, 30)),
            ("Friday", datetime(2025, 6, 13)),
            ("3 days ago", datetime(2025, 6, 13, 10, 30)),
        ],
    )
    def test_date_obj_selection_is_unchanged(self, date_string, expected):
        parser = DateDataParser(settings={"RELATIVE_BASE": BASE})
        parser.get_interval_data(date_string)
        assert parser.get_date_data(date_string).date_obj == expected

    def test_get_date_tuple_shape_is_unchanged(self):
        parser = DateDataParser(settings={"RELATIVE_BASE": BASE})
        date_tuple = parser.get_date_tuple("March 2025")
        assert date_tuple == (datetime(2025, 3, 16), "month", "en")

    def test_repr_has_no_interval_metadata(self):
        parser = DateDataParser(settings={"RELATIVE_BASE": BASE})
        date_data = parser.get_date_data("March 2025")
        assert repr(date_data) == (
            "DateData(date_obj=datetime.datetime(2025, 3, 16, 0, 0),"
            " period='month', locale='en', parts=('year', 'month'))"
        )

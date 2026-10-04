from datetime import datetime

import pytest
from parameterized import param, parameterized

from dateparser import DateDataParser, parse_date_range
from dateparser.ranges import DateRangeParser

BASE = {"RELATIVE_BASE": datetime(2026, 10, 4)}


def _range(date_string, **kwargs):
    kwargs.setdefault("settings", BASE)
    return parse_date_range(date_string, **kwargs)


class TestExplicitRanges:
    @parameterized.expand(
        [
            # Chinese: repeated day inherits month and year.
            param(
                "2025年3月3日至5日",
                languages=["zh"],
                start=datetime(2025, 3, 3),
                end=datetime(2025, 3, 5),
                interval_end=datetime(2025, 3, 6),
            ),
            param(
                "2025年3月3日到5日",
                languages=["zh"],
                start=datetime(2025, 3, 3),
                end=datetime(2025, 3, 5),
                interval_end=datetime(2025, 3, 6),
            ),
            param(
                "3月3日至5日",
                languages=["zh"],
                start=datetime(2026, 3, 3),
                end=datetime(2026, 3, 5),
                interval_end=datetime(2026, 3, 6),
            ),
            param(
                "2025年3月3日至3月5日",
                languages=["zh"],
                start=datetime(2025, 3, 3),
                end=datetime(2025, 3, 5),
                interval_end=datetime(2025, 3, 6),
            ),
            # English: explicit forms with dashes and "to".
            param(
                "June 12-14, 2021",
                languages=["en"],
                start=datetime(2021, 6, 12),
                end=datetime(2021, 6, 14),
                interval_end=datetime(2021, 6, 15),
            ),
            param(
                "12-14 June 2021",
                languages=["en"],
                start=datetime(2021, 6, 12),
                end=datetime(2021, 6, 14),
                interval_end=datetime(2021, 6, 15),
            ),
            param(
                "30 Dec to 2 Jan 2026",
                languages=["en"],
                start=datetime(2025, 12, 30),
                end=datetime(2026, 1, 2),
                interval_end=2026 and datetime(2026, 1, 3),
            ),
            param(
                "30 Dec–2 Jan 2026",
                languages=["en"],
                start=datetime(2025, 12, 30),
                end=datetime(2026, 1, 2),
                interval_end=datetime(2026, 1, 3),
            ),
            param(
                "2025-12-30 to 2026-01-02",
                languages=["en"],
                start=datetime(2025, 12, 30),
                end=datetime(2026, 1, 2),
                interval_end=datetime(2026, 1, 3),
            ),
            # Only the end year given forces the start year.
            param(
                "12月31日至1月2日 2026",
                languages=["zh"],
                start=datetime(2025, 12, 31),
                end=datetime(2026, 1, 2),
                interval_end=datetime(2026, 1, 3),
            ),
            param(
                "2025年12月30日至1月2日",
                languages=["zh"],
                start=datetime(2025, 12, 30),
                end=datetime(2026, 1, 2),
                interval_end=datetime(2026, 1, 3),
            ),
            # Cross-month within a shared year.
            param(
                "2025年1月31日至2月2日",
                languages=["zh"],
                start=datetime(2025, 1, 31),
                end=datetime(2025, 2, 2),
                interval_end=datetime(2025, 2, 3),
            ),
        ]
    )
    def test_parse_range(
        self, date_string, start, end, interval_end, languages
    ):
        result = _range(date_string, languages=languages)
        assert result is not None
        assert result.start == start
        assert result.end == end
        assert result.interval_start == start
        assert result.interval_end == interval_end
        assert result.ambiguous is False
        assert result.start_text and result.end_text
        assert result.connector

    def test_period_day_month_year(self):
        month_range = _range("March 2025 to May 2025", languages=["en"])
        assert month_range.period == "month"
        assert month_range.start == datetime(2025, 3, 1)
        assert month_range.end == datetime(2025, 5, 1)
        assert month_range.interval_end == datetime(2025, 6, 1)

        year_range = _range("2025 to 2027", languages=["en"])
        assert year_range.period == "year"
        assert year_range.start == datetime(2025, 1, 1)
        assert year_range.interval_end == datetime(2028, 1, 1)

        # A four-digit/two-digit pair is a year-month, not month/day.
        ym_range = _range("2025-11 to 2026-01", languages=["en"])
        assert ym_range.period == "month"
        assert ym_range.start == datetime(2025, 11, 1)
        assert ym_range.end == datetime(2026, 1, 1)
        assert ym_range.interval_end == datetime(2026, 2, 1)

    def test_time_endpoint_is_exclusive_as_written(self):
        result = _range(
            "2025-03-03 10:00 to 2025-03-05 12:30", languages=["en"]
        )
        assert result.period == "day"
        assert result.start == datetime(2025, 3, 3, 10, 0)
        assert result.end == datetime(2025, 3, 5, 12, 30)
        # An end with a time is kept, not advanced to the next day.
        assert result.interval_end == datetime(2025, 3, 5, 12, 30)

    def test_date_only_end_advances_but_time_end_does_not(self):
        both_times = _range(
            "2025-03-03 09:00 to 2025-03-03 17:00", languages=["en"]
        )
        assert both_times.interval_end == datetime(2025, 3, 3, 17, 0)

        date_only_end = _range(
            "2025-03-03 00:00 to 2025-03-05", languages=["en"]
        )
        assert date_only_end.interval_end == datetime(2025, 3, 6)

    def test_time_only_end_inherits_the_other_date(self):
        result = _range(
            "2025-03-03 10:00 to 12:00", languages=["en"]
        )
        assert result.start == datetime(2025, 3, 3, 10, 0)
        assert result.end == datetime(2025, 3, 3, 12, 0)

        same_day = _range("10:00 to 12:00", languages=["en"])
        assert same_day.start.date() == datetime(2026, 10, 4).date()
        assert same_day.end.date() == datetime(2026, 10, 4).date()

    def test_iso_dashes_are_not_connectors(self):
        assert _range("2025-03-03", languages=["en"]) is None
        assert _range("2025-03-03-2025-03-05", languages=["en"]) is None
        assert _range("06-04-25-06-09-25", languages=["en"]) is None
        # Whitespace around a dash makes it a connector.
        result = _range("2025-03-03 - 2025-03-05", languages=["en"])
        assert result is not None
        assert result.start == datetime(2025, 3, 3)
        assert result.end == datetime(2025, 3, 5)

    def test_bare_numbers_without_month_anchor_are_not_ranges(self):
        assert _range("12-14", languages=["en"]) is None

    @parameterized.expand(
        [
            param("03-03 to 03-05", datetime(2026, 3, 3), datetime(2026, 3, 5)),
            param(
                "03-03-2025 to 03-05-2025",
                datetime(2025, 3, 3),
                datetime(2025, 3, 5),
            ),
            param(
                "2025/03/03 to 2025/03/05",
                datetime(2025, 3, 3),
                datetime(2025, 3, 5),
            ),
        ]
    )
    def test_numeric_halves_follow_locale_date_order(
        self, date_string, start, end
    ):
        result = _range(date_string, languages=["en"])
        assert result is not None
        assert result.start == start
        assert result.end == end

    def test_single_date_still_uses_original_entry(self):
        # The range entry leaves standalone dates untouched.
        assert parse_date_range("2025年3月3日", settings=BASE) is None
        assert DateDataParser(settings=BASE).get_date_data(
            "2025年3月3日"
        ).date_obj == datetime(2025, 3, 3)

    @parameterized.expand(
        [
            param("today to tomorrow", languages=["en"]),
            param("2025-03-03 to yesterday", languages=["en"]),
            param("monday to friday", languages=["en"]),
        ]
    )
    def test_relative_and_weekday_expressions_are_not_ranges(
        self, date_string, languages
    ):
        assert _range(date_string, languages=languages) is None


class TestAmbiguity:
    @parameterized.expand(
        [
            # Cross-year boundary with neither year written: not unique.
            param("12月31日至1月2日", languages=["zh"]),
            # Days inside one month in reversed order: no valid carry.
            param("2025年3月5日至3日", languages=["zh"]),
            # A New Year's crossing for same-month days with no year.
            param("1月3日至1月2日", languages=["zh"]),
        ]
    )
    def test_ambiguous(self, date_string, languages):
        result = _range(date_string, languages=languages)
        assert result is not None
        assert result.ambiguous is True
        assert result.start is None

    def test_leap_day_without_year_is_ambiguous(self):
        result = _range("2月29日至3月1日", languages=["zh"])
        assert result is not None
        assert result.ambiguous is True
        assert datetime(2024, 2, 29) in result.candidates
        assert datetime(2028, 2, 29) in result.candidates

    @parameterized.expand(
        [
            param("2024年2月29日至3月1日", datetime(2024, 2, 29)),
        ]
    )
    def test_leap_day_with_explicit_leap_year(self, date_string, start):
        result = _range(date_string, languages=["zh"])
        assert result is not None
        assert result.ambiguous is False
        assert result.start == start


class TestInvalidRanges:
    @parameterized.expand(
        [
            param("2025年2月29日至3月1日", languages=["zh"]),  # 2025 is not leap
            param("2025年2月30日至3月1日", languages=["zh"]),  # Feb 30
            param("2025年4月31日至5月2日", languages=["zh"]),  # April 31
            param("2025年3月5日至3日", languages=["zh"]),
        ]
    )
    def test_impossible_or_contradictory_dates_return_none_or_ambiguous(
        self, date_string, languages
    ):
        result = _range(date_string, languages=languages)
        assert result is None or result.ambiguous

    def test_month_end_stays_in_month(self):
        result = _range("2025年2月26日至2月28日", languages=["zh"])
        assert result is not None
        assert result.start == datetime(2025, 2, 26)
        assert result.end == datetime(2025, 2, 28)
        assert result.interval_end == datetime(2025, 3, 1)


class TestTimeZones:
    def test_mixed_awareness_raises(self):
        with pytest.raises(ValueError, match="inconsistent"):
            _range(
                "2025-03-03 10:00 UTC to 2025-03-05 12:00",
                languages=["en"],
            )

    def test_different_time_zones_preserved_and_convertible(self):
        result = _range(
            "2025-03-03 10:00 UTC to 2025-03-05 12:00 EST",
            languages=["en"],
        )
        assert result is not None
        assert result.start.tzinfo is not None
        assert result.end.tzinfo is not None

        converted = parse_date_range(
            "2025-03-03 10:00 UTC to 2025-03-05 12:00 UTC",
            languages=["en"],
            settings={**BASE, "TO_TIMEZONE": "Asia/Shanghai"},
        )
        assert converted.start.utcoffset().total_seconds() == 8 * 3600


def test_parser_instance_method():
    parser = DateDataParser(languages=["zh"], settings=BASE)
    result = parser.get_date_range_data("2025年3月3日至5日")
    assert result is not None
    assert result.start == datetime(2025, 3, 3)
    assert result.end == datetime(2025, 3, 5)

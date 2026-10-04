"""Parse explicit date ranges such as "2025年3月3日至5日" or
"30 Dec-2 Jan 2026" into standardized half-open intervals.

Only explicit, unambiguous forms are handled here. Relative expressions and
date orders that cannot be determined uniquely fall back to ordinary
single-date parsing or are reported as :attr:`DateRangeData.ambiguous`
instead of silently choosing the nearest date.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

import regex as re

from dateparser.conf import Settings, apply_settings
from dateparser.date import DASH_LOOK_ALIKE_CHARS, DateDataParser

# Connectors written with letters. Only forms that cannot be confused with
# date parts are recognized; adding a language only needs new entries here.
_WORD_CONNECTORS = (
    ("latin", "to"),
    ("latin", "until"),
    ("latin", "till"),
    ("latin", "thru"),
    ("latin", "through"),
    ("cjk", "至"),
    ("cjk", "到"),
)

_RE_DASH = re.compile("[" + re.escape("-" + "".join(DASH_LOOK_ALIKE_CHARS)) + "]")

_CJK = "\u4e00-\u9fff"
_CJK_YEAR = "年"
_CJK_MONTH = "月"
_CJK_DAY_MARK = "日号"

_MONTH_NAME = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?"
)
_RE_MONTH_NAME = re.compile(rf"(?i)\b(?:{_MONTH_NAME})\b")
_RE_FOUR_DIGIT = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_RE_TIME_TOKEN = re.compile(
    r"(?i)\b(\d{1,2}(?::\d{2})+(?::\d{2})?(?:\.\d+)?|\d{1,2}\s*(?:am|pm))\b"
)

# Relative words that point away from an explicit date ("today", "yesterday",
# ...). A range end written with one of these is not an explicit form and is
# therefore left to the single-date entry point.
_RE_RELATIVE_WORDS = re.compile(
    r"(?i)\b(?:today|tomorrow|yesterday|now|tonight|yday)\b"
)


@dataclass
class PartialDate:
    """Date components explicitly written for one end of a range.

    Components absent from the text are ``None`` — they are never silently
    filled here; :class:`DateRangeParser` inherits them from the other end or
    the reference date following the public date rules.
    """

    year: int | None = None
    month: int | None = None
    day: int | None = None
    time_of_day: time | None = None
    tzinfo: Any = None
    raw: str = ""

    @property
    def has_time(self) -> bool:
        return self.time_of_day is not None

    @property
    def granularity(self) -> str:
        if self.time_of_day is not None:
            return "time"
        if self.day is not None:
            return "day"
        if self.month is not None:
            return "month"
        if self.year is not None:
            return "year"
        return "none"


@dataclass
class DateRangeData:
    """Result of parsing an explicit date range.

    ``start`` and ``end`` are the two ends of a *closed* interval. Missing
    year/month/day parts are inherited from the other end, or from the
    reference date by the public date rules. ``interval_start`` and
    ``interval_end`` express the same range as a standardized half-open
    interval ``[interval_start, interval_end)``: a date-only end advances to
    midnight of the following day (or period), while an end that carries a
    time is kept exactly as written.

    When the text does not determine a single range, ``ambiguous`` is ``True``
    and ``candidates`` lists the starts that remain possible.
    """

    start: datetime | None = None
    end: datetime | None = None
    interval_start: datetime | None = None
    interval_end: datetime | None = None
    start_text: str | None = None
    end_text: str | None = None
    connector: str | None = None
    period: str = "day"
    locale: str | None = None
    ambiguous: bool = False
    reason: str | None = None
    candidates: list[datetime] = field(default_factory=list)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


def _find_connectors(text: str) -> list[tuple[int, int, str]]:
    """Return ``(start, stop, value)`` spans of every potential connector.

    Latin connectors need word boundaries (so ``"today"`` is not a range).
    CJK connectors are self-delimiting because Chinese has no spaces, so
    they need no boundary check. Dashes are accepted or rejected later in
    :meth:`DateRangeParser._is_range_dash`.
    """

    spans: list[tuple[int, int, str]] = []
    lowered = text.lower()
    for kind, word in _WORD_CONNECTORS:
        if kind == "latin":
            pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])")
        else:
            # CJK connectors are self-delimiting characters: Chinese is
            # written without spaces, so no word boundary is required.
            pattern = re.compile(re.escape(word))
        for match in pattern.finditer(lowered):
            spans.append((match.start(), match.end(), text[match.start() : match.end()]))
    for match in _RE_DASH.finditer(text):
        spans.append((match.start(), match.end(), match.group()))
    spans.sort()
    return spans


def _adjacent_char(text: str, index: int, step: int) -> str:
    pos = index
    while 0 <= pos < len(text):
        if not text[pos].isspace():
            return text[pos]
        pos += step
    return ""


def _numeric_run_edge(text: str, index: int, step: int) -> int:
    pos = index
    while 0 <= pos < len(text) and text[pos].isdigit():
        pos += step
    return pos


class DateRangeParser:
    """Parse explicit, two-ended date ranges.

    Reuses :class:`~dateparser.date.DateDataParser` to recognize the date
    parts of each end and to read times/time zones. This class adds connector
    detection, explicit-part inheritance and the controlled cross-month /
    cross-year carry rules.
    """

    @apply_settings
    def __init__(
        self,
        languages: Iterable[str] | None = None,
        locales: Iterable[str] | None = None,
        region: str | None = None,
        settings: Settings | dict[str, Any] | None = None,
        detect_languages_function: Callable[..., list[str]] | None = None,
    ) -> None:
        assert isinstance(settings, Settings)
        self._settings = settings
        self._languages = list(languages) if languages else None
        self._locales = locales
        self._region = region
        self._detect_languages_function = detect_languages_function

    # -- connector detection ------------------------------------------------

    def _is_range_dash(self, text: str, start: int, stop: int) -> bool:
        """A dash is a range connector only in date-grammar context.

        A hyphen glued between numbers is an internal date separator (as in
        ``"2025-03-03"`` or ``"06-04-25"``) whenever one of its sides runs on
        into another dash-glued number. A dash next to a word (a month name
        or a CJK marker), a whitespace-delimited dash, or a dash joining two
        number runs with a month anchor elsewhere is a genuine connector.
        """

        left_char = _adjacent_char(text, start - 1, -1)
        right_char = _adjacent_char(text, stop, 1)
        left_glued = start > 0 and not text[start - 1].isspace()
        right_glued = stop < len(text) and not text[stop].isspace()

        if left_glued and left_char.isdigit():
            run_start = _numeric_run_edge(text, start - 1, -1) + 1
            if run_start > 0 and _RE_DASH.fullmatch(text[run_start - 1]):
                return False
        if right_glued and right_char.isdigit():
            run_end = _numeric_run_edge(text, stop, 1)
            if run_end < len(text) and _RE_DASH.fullmatch(text[run_end]):
                return False

        return True

    def _connector_candidates(self, text: str) -> list[tuple[int, int, str]]:
        candidates = [
            (start, stop, value)
            for start, stop, value in _find_connectors(text)
            if _RE_DASH.fullmatch(value) is None
            or self._is_range_dash(text, start, stop)
        ]
        # Word connectors win over dashes, then the leftmost connector wins.
        candidates.sort(key=lambda c: (_RE_DASH.fullmatch(c[2]) is not None, c[0]))
        return candidates

    # -- component extraction -----------------------------------------------

    def _new_parser(self, text: str) -> DateDataParser:
        languages = self._languages
        if (
            languages is None
            and self._locales is None
            and re.search(rf"[{_CJK}]", text)
        ):
            # CJK date tokens alone can make language detection prefer
            # Japanese; the Chinese connectors supported here imply Chinese.
            languages = ["zh"]
        return DateDataParser(
            languages=languages,
            locales=self._locales,
            region=self._region,
            settings=self._settings,
            detect_languages_function=self._detect_languages_function,
        )

    @staticmethod
    def _extract_cjk(text: str) -> PartialDate:
        partial = PartialDate(raw=text)
        consumed: list[tuple[int, int]] = []

        def first(pattern: str, group: int = 1) -> int | None:
            for match in re.finditer(pattern, text):
                consumed.append(match.span(group))
                return int(match.group(group))
            return None

        partial.year = first(r"(\d{2,4})\s*" + _CJK_YEAR)
        if partial.year is None:
            year_match = _RE_FOUR_DIGIT.search(text)
            if year_match:
                partial.year = int(year_match.group())
                consumed.append(year_match.span(1))
        partial.month = first(r"(\d{1,2})\s*" + _CJK_MONTH)
        partial.day = first(r"(\d{1,2})\s*([" + _CJK_DAY_MARK + r"])")

        residual = list(text)
        for a, b in sorted(consumed, reverse=True):
            del residual[a:b]
        bare = re.findall(r"(?<!\d)(\d{1,4})(?!\d)", "".join(residual))
        if bare and partial.day is None:
            partial.day = int(bare[0])
        return partial

    @staticmethod
    def _extract_western(
        text: str, parsed_year: int | None, parsed_month_day: tuple[int, int] | None
    ) -> PartialDate:
        partial = PartialDate(raw=text)

        month_match = _RE_MONTH_NAME.search(text)
        if month_match:
            names = ("jan", "feb", "mar", "apr", "may", "jun",
                     "jul", "aug", "sep", "oct", "nov", "dec")
            partial.month = names.index(month_match.group()[:3].lower()) + 1

        year_match = _RE_FOUR_DIGIT.search(text)
        if year_match:
            partial.year = int(year_match.group())

        # Explicit year-month pairs ("2025-11" or "11/2025") name a month and
        # must not be read as a month/day pair under a numeric date order.
        ym_match = re.fullmatch(
            r"\s*(?:(\d{4})\s*[/.\-]\s*(\d{1,2})|(\d{1,2})\s*[/.\-]\s*(\d{4}))\s*",
            text,
        )
        if ym_match and partial.month is None:
            if ym_match.group(2):
                partial.year, partial.month = int(ym_match.group(1)), int(
                    ym_match.group(2)
                )
            else:
                partial.month, partial.year = int(ym_match.group(3)), int(
                    ym_match.group(4)
                )
            return partial

        date_part = _RE_TIME_TOKEN.sub("", text)
        numbers = [int(n) for n in re.findall(r"(?<!\d)(\d{1,2})(?!\d)", date_part)]
        numbers = [n for n in numbers if n != partial.year]
        if partial.month is not None and numbers:
            partial.day = numbers[0]
        elif (
            partial.month is None
            and partial.day is None
            and parsed_month_day is not None
        ):
            # Numeric half such as "03-05"; the month/day order follows the
            # locale order, the public disambiguation rule.
            partial.month, partial.day = parsed_month_day
            year_in_text = _RE_FOUR_DIGIT.search(text)
            if partial.year is None and year_in_text:
                partial.year = parsed_year
        elif partial.month is None and partial.day is None and numbers:
            # Bare day numbers whose month is inherited (e.g. "14" in
            # "June 12-14, 2021"); a month anchor is required later.
            partial.day = numbers[0]
        return partial

    def _extract_partial(self, text: str, parser: DateDataParser) -> PartialDate | None:
        if not re.search(rf"[\dA-Za-z{_CJK}]", text):
            return None
        if _RE_RELATIVE_WORDS.search(text):
            return None

        data = parser.get_date_data(text)

        is_cjk = bool(re.search("[" + _CJK_YEAR + _CJK_MONTH + _CJK_DAY_MARK + "]", text))
        if is_cjk:
            partial = self._extract_cjk(text)
        else:
            parsed_year = (
                data.date_obj.year
                if data.date_obj and "year" in data.parts
                else None
            )
            parsed_month_day = (
                (data.date_obj.month, data.date_obj.day)
                if data.date_obj
                and "month" in data.parts
                and "day" in data.parts
                and not _RE_MONTH_NAME.search(text)
                and self._looks_like_numeric_date(text, data)
                else None
            )
            partial = self._extract_western(
                text, parsed_year, parsed_month_day
            )

        # A lone 1-2 digit number can only be a day, inherited once the other
        # end proves a month exists.
        if (
            partial.year is None
            and partial.month is None
            and partial.day is None
            and re.fullmatch(r"\s*\d{1,2}\s*", text)
        ):
            partial.day = int(re.sub(r"\D", "", text))

        if data.date_obj is not None and "time" in data.parts:
            partial.time_of_day = data.date_obj.time()
            partial.tzinfo = data.date_obj.tzinfo

        # Explicit forms need at least one concrete date token or an explicit
        # clock time ("12:00"). Bare relative/weekday words never qualify.
        has_date_part = (
            partial.year is not None
            or partial.month is not None
            or partial.day is not None
        )
        if not has_date_part and partial.time_of_day is None:
            return None
        # A half with no digit at all can only be a weekday or a relative
        # word; ranges of such words are not explicit forms.
        if not re.search(r"\d", text) and partial.time_of_day is None:
            return None
        return partial

    @staticmethod
    def _looks_like_numeric_date(text: str, data: Any) -> bool:
        """Whether a number-only half really carries a month/day pair.

        A bare clock time ("12:00") parses onto a concrete day but does not
        write one, so it must not be mistaken for an explicit date.
        """

        without_time = _RE_TIME_TOKEN.sub("", text)
        if re.search(r"[/.\-]\s*\d", without_time) or re.search(
            r"\d\s*[/.\-]", without_time
        ):
            return True
        # Two or more numbers outside the clock time spell a date.
        return len(re.findall(r"\d+", without_time)) >= 2

    # -- resolution ---------------------------------------------------------

    @staticmethod
    def _is_valid(year: int, month: int, day: int) -> bool:
        return 1 <= month <= 12 and 1 <= day <= calendar.monthrange(year, month)[1]

    def _reference_year(self) -> int:
        if self._settings.RELATIVE_BASE:
            return self._settings.RELATIVE_BASE.year
        return datetime.now().year

    def _reference_month(self, known_year: int | None) -> int:
        base = self._settings.RELATIVE_BASE
        if base and (known_year is None or base.year == known_year):
            return base.month
        return 1

    @staticmethod
    def _period_day() -> int:
        # A missing day bounds a period at its first day deterministically.
        return 1

    def _fill_side(
        self, partial: PartialDate, year: int, month: int
    ) -> tuple[int, int, int]:
        y = partial.year if partial.year is not None else year
        m = partial.month if partial.month is not None else month
        if partial.day is not None:
            d = partial.day
        elif partial.has_time:
            # A clock time with no date is resolved on the reference day,
            # the same rule the single-date parser uses.
            base = self._settings.RELATIVE_BASE
            d = base.day if base and (base.year == y and base.month == m) else 1
        else:
            d = self._period_day()
        return y, m, d

    def _datetime(self, partial: PartialDate, y: int, m: int, d: int) -> datetime:
        if partial.has_time:
            dt = datetime.combine(datetime(y, m, d), partial.time_of_day)
            if partial.tzinfo is not None:
                dt = dt.replace(tzinfo=partial.tzinfo)
            return dt
        try:
            return datetime(y, m, d)
        except ValueError:
            # Impossible calendar date (e.g. Feb 30); validity checks report
            # it. Clamp the day so comparisons never raise.
            return datetime(y, m, min(d, calendar.monthrange(y, m)[1]))

    @staticmethod
    def _period(granularities: set[str]) -> str:
        if granularities <= {"year"}:
            return "year"
        if granularities <= {"month", "year"}:
            return "month"
        # Any explicit day (or a time, which always resolves onto a day)
        # makes this a day-level closed range; the time only decides whether
        # the half-open end advances to the next day.
        return "day"

    def _merge(self, left: PartialDate, right: PartialDate) -> DateRangeData:
        result = DateRangeData(period=self._period({left.granularity, right.granularity}))

        # Time zone inconsistency must be surfaced explicitly rather than
        # guessed: one aware and one naive end cannot be ordered safely.
        if (left.tzinfo is not None) != (right.tzinfo is not None):
            which = "start" if left.tzinfo is not None else "end"
            raise ValueError(
                f"the {which} of the range carries a time zone while the "
                "other end is timezone-naive; the time zones are inconsistent"
            )

        # ---- 1. inheritance of shared explicit parts ---------------------
        shared_year = left.year if left.year is not None else right.year
        if shared_year is None:
            shared_year = self._reference_year()
        shared_month = left.month if left.month is not None else right.month
        if shared_month is None:
            shared_month = self._reference_month(shared_year)

        ly0, lm0, ld0 = self._fill_side(left, shared_year, shared_month)
        ry0, rm0, rd0 = self._fill_side(right, shared_year, shared_month)

        # A time-only end ("12:00" in "2025-03-03 10:00 to 12:00") gives no
        # date of its own, so it inherits the full date the other end gives.
        left_dateless = left.day is None and left.month is None and left.year is None
        right_dateless = right.day is None and right.month is None and right.year is None
        if left_dateless and not right_dateless:
            ly0, lm0, ld0 = ry0, rm0, rd0
        elif right_dateless and not left_dateless:
            ry0, rm0, rd0 = ly0, lm0, ld0

        day_level = result.period == "day"

        # ---- 2. validate calendar dates -----------------------------------
        left_valid = (
            left.day is None or self._is_valid(ly0, lm0, ld0)
        )
        right_valid = (
            right.day is None or self._is_valid(ry0, rm0, rd0)
        )

        if day_level and not left_valid and not (lm0 == 2 and ld0 == 29 and left.year is None):
            return result
        if day_level and not right_valid and not (rm0 == 2 and rd0 == 29 and right.year is None):
            return result

        start0 = self._datetime(left, ly0, lm0, ld0)
        end0 = self._datetime(right, ry0, rm0, rd0)

        leap_ambiguous = (
            (not left_valid and lm0 == 2 and ld0 == 29 and left.year is None)
            or (not right_valid and rm0 == 2 and rd0 == 29 and right.year is None)
        )
        if day_level and leap_ambiguous:
            feb_partial = left if (not left_valid and lm0 == 2) else right
            return self._ambiguous(
                result, feb_partial, 2, shared_year,
                "Feb 29 with no explicit year",
            )

        if start0 <= end0:
            return self._finalize(result, left, right, (ly0, lm0, ld0), (ry0, rm0, rd0))

        # ---- 3. reversed ends: an explicit cross-month/year carry only ----
        if not day_level:
            # Period-level ends (month/year granularity) resolve to period
            # starts, so a reversed order simply contradicts the text.
            return result

        same_month = (ly0, lm0) == (ry0, rm0)
        left_year_explicit = left.year is not None
        right_year_explicit = right.year is not None
        both_years_explicit = left_year_explicit and right_year_explicit

        if same_month:
            # Two explicit days in one month in the wrong order, or a carry
            # across New Year's boundary with no year given: never guess.
            if not both_years_explicit:
                return self._ambiguous(
                    result, right, shared_month, shared_year,
                    "days cross a year boundary with no explicit year",
                )
            return result  # contradiction (e.g. 5..3 March 2025)

        # Months differ. When both years are written the ends simply
        # contradict each other; when neither is written the New Year
        # boundary the range crosses is undetermined: ambiguous, not guessed.
        if both_years_explicit:
            return result
        if not left_year_explicit and not right_year_explicit:
            return self._ambiguous(
                result, right, shared_month, shared_year,
                "cross-year carry is not uniquely determined",
            )

        if right_year_explicit:
            # "12月31日..1月2日 2026": the left end belongs to 2025.
            ly1 = ry0 - 1
            candidate_left = (ly1, lm0, ld0)
            if not self._is_valid(*candidate_left):
                return result
            return self._finalize(result, left, right, candidate_left, (ry0, rm0, rd0))

        # left year explicit ("2025-12-30 .. 2 Jan"): the right end is 2026.
        ry1 = ly0 + 1
        candidate_right = (ry1, rm0, rd0)
        if not self._is_valid(*candidate_right):
            return result
        return self._finalize(result, left, right, (ly0, lm0, ld0), candidate_right)

    def _ambiguous(
        self,
        result: DateRangeData,
        partial: PartialDate,
        month: int,
        year: int,
        reason: str,
    ) -> DateRangeData:
        result.ambiguous = True
        result.reason = reason
        candidates: list[datetime] = []
        for candidate_year in self._neighbor_years(partial, month, year):
            d = partial.day or 1
            if self._is_valid(candidate_year, month, d):
                candidates.append(datetime(candidate_year, month, d))
        result.candidates = sorted(set(candidates))
        return result

    def _neighbor_years(
        self, partial: PartialDate, month: int, year: int
    ) -> list[int]:
        if partial.day == 29 and month == 2:
            prev_year = year - 1
            while not calendar.isleap(prev_year):
                prev_year -= 1
            next_year = year + 1
            while not calendar.isleap(next_year):
                next_year += 1
            return [prev_year, next_year]
        return [year - 1, year, year + 1]

    def _finalize(
        self,
        result: DateRangeData,
        left: PartialDate,
        right: PartialDate,
        left_ymd: tuple[int, int, int],
        right_ymd: tuple[int, int, int],
    ) -> DateRangeData:
        start = self._datetime(left, *left_ymd)
        end = self._datetime(right, *right_ymd)
        start, end = self._apply_timezones(left, right, start, end)

        result.start = start
        result.end = end
        result.interval_start = start
        result.interval_end = self._interval_end(right, end)
        return result

    @staticmethod
    def _interval_end(right: PartialDate, end: datetime) -> datetime:
        if right.has_time:
            return end
        if right.day is None:
            if right.month is not None:
                # Month-only end: first instant of the following month.
                if end.month == 12:
                    return datetime(end.year + 1, 1, 1, tzinfo=end.tzinfo)
                return datetime(end.year, end.month + 1, 1, tzinfo=end.tzinfo)
            if right.year is not None:
                return datetime(end.year + 1, 1, 1, tzinfo=end.tzinfo)
        return end + timedelta(days=1)

    def _apply_timezones(
        self,
        left: PartialDate,
        right: PartialDate,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime, datetime]:
        left_aware = left.tzinfo is not None
        right_aware = right.tzinfo is not None
        if left_aware != right_aware:
            which = "start" if left_aware else "end"
            raise ValueError(
                f"the {which} of the range carries a time zone while the "
                "other end is timezone-naive; the time zones are inconsistent"
            )
        if left_aware:
            start = start.replace(tzinfo=left.tzinfo)
            end = end.replace(tzinfo=right.tzinfo)
            target = self._settings.TO_TIMEZONE
            if target:
                from dateparser.utils import apply_timezone

                start = apply_timezone(start, target)
                end = apply_timezone(end, target)
        return start, end

    # -- public entry -------------------------------------------------------

    @staticmethod
    def _bare_number(text: str) -> bool:
        return bool(re.fullmatch(r"\s*\d{1,2}\s*", text))

    def get_date_range_data(
        self,
        date_string: str,
        date_formats: Iterable[str] | None = None,
    ) -> DateRangeData:
        if not isinstance(date_string, str):
            raise TypeError("Input type must be str")

        text = date_string.strip()
        parser = self._new_parser(text)

        for cstart, cstop, connector in self._connector_candidates(text):
            left_text = text[:cstart].strip(" ,\t，")
            right_text = text[cstop:].strip(" ,\t，")
            if not left_text or not right_text:
                continue

            left = self._extract_partial(left_text, parser)
            right = self._extract_partial(right_text, parser)
            if left is None or right is None:
                continue

            month_anchor = left.month is not None or right.month is not None
            if (
                self._bare_number(left_text) or self._bare_number(right_text)
            ) and not month_anchor:
                # "12-14" with no month anywhere is not a date range; the
                # number would be interpreted by the single-date parser.
                continue

            result = self._merge(left, right)
            if result.start is None and not result.ambiguous:
                continue
            result.start_text = left_text
            result.end_text = right_text
            result.connector = connector
            result.locale = parser.languages[0] if parser.languages else None
            return result

        return DateRangeData()


_default_range_parser = DateRangeParser()


@apply_settings
def parse_date_range(
    date_string: str,
    date_formats: Iterable[str] | None = None,
    languages: Iterable[str] | None = None,
    locales: Iterable[str] | None = None,
    region: str | None = None,
    settings: Settings | dict[str, Any] | None = None,
    detect_languages_function: Callable[..., list[str]] | None = None,
) -> DateRangeData | None:
    """Parse an explicit date *range* into a standardized half-open interval.

    Supports explicit Chinese and English forms such as
    ``"2025年3月3日至5日"`` and ``"30 Dec-2 Jan 2026"``. The two ends share
    the year and month they do not repeat. A cross-month or cross-year carry
    is applied only when the text forces a unique one; when the range can be
    read in more than one way, a :class:`DateRangeData` with
    :attr:`~DateRangeData.ambiguous` set is returned instead of a guessed
    date. Strings that are not explicit ranges return ``None``; use
    :func:`dateparser.parse` for standalone dates.

    :param date_string:
        A string holding two explicit date ends joined by a range connector.
    :type date_string: str
    :param date_formats:
        Optional strptime-style formats, passed through for each end.
    :type date_formats: list
    :param languages:
        Language codes, e.g. ``['en', 'zh']``.
    :type languages: list
    :param locales:
        Locale codes, e.g. ``['fr-PF']``.
    :type locales: list
    :param region:
        Region code, e.g. ``'CN'``.
    :type region: str
    :param settings:
        Behavior customization as defined by :class:`dateparser.conf.Settings`.
    :type settings: dict
    :param detect_languages_function:
        Language detection callback, as for :func:`dateparser.parse`.
    :return:
        :class:`DateRangeData` with the closed ends and the half-open
        interval, or ``None`` when the string is not an explicit range.
    :rtype: :class:`DateRangeData` or ``None``
    :raises ValueError:
        When the two ends use inconsistent time zone awareness (one gives a
        time zone while the other is timezone-naive).
    """
    assert isinstance(settings, Settings)
    parser = _default_range_parser
    if (
        languages
        or locales
        or region
        or detect_languages_function
        or not settings._default
    ):
        parser = DateRangeParser(
            languages=languages,
            locales=locales,
            region=region,
            settings=settings,
            detect_languages_function=detect_languages_function,
        )

    data = parser.get_date_range_data(date_string, date_formats)
    if data.start is None and not data.ambiguous:
        return None
    return data

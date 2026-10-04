"""Interval result mode.

Instead of a single :class:`~datetime.datetime`, this module computes the
minimal interpretable time interval that the explicitly parsed parts of a
date string determine, together with precision metadata. Interval bounds
follow calendar boundaries: a month is a calendar month (not 30 days) and a
day in a time zone that crosses a DST transition is not 24 hours.
"""

from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any

from pytz.exceptions import InvalidTimeError

from dateparser.conf import Settings

if TYPE_CHECKING:
    from dateparser.date import DateData

PART_SOURCE_INPUT = "input"
"""The part was explicitly given in the parsed string."""

PART_SOURCE_RELATIVE_BASE = "relative_base"
"""The part was completed from ``RELATIVE_BASE`` or the current time."""

PART_SOURCE_PREFERENCE = "preference"
"""The part was inferred from a preference setting, e.g.
``PREFER_DAY_OF_MONTH`` or ``PARTS_OF_DAY``."""

_DATE_PARTS = ("year", "month", "day")
_TIME_PARTS = ("hour", "minute", "second", "millisecond", "microsecond")


class DateInterval:
    """
    Class that represents the minimal interpretable time interval of a parsed
    date string, with precision metadata. It can be accessed with square
    brackets like a dict object.

    .. versionadded:: VERSION
    """

    def __init__(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        precision: str | None = None,
        part_sources: dict[str, str] | None = None,
        base: datetime | None = None,
        meaning: str | None = None,
        reason: str | None = None,
    ) -> None:
        self.start = start
        """Inclusive interval start, or ``None`` when it cannot be determined
        (see :attr:`reason`)."""

        self.end = end
        """Exclusive interval end, or ``None`` when it cannot be determined
        (see :attr:`reason`)."""

        self.precision = precision
        """Finest meaningful part of the parsed string: ``"year"``,
        ``"month"``, ``"day"``, ``"hour"``, ``"minute"``, ``"second"``,
        ``"millisecond"`` or ``"microsecond"``."""

        self.part_sources = part_sources or {}
        """Source of each part of the result: ``"input"`` when explicitly
        given in the parsed string, ``"relative_base"`` when completed from
        ``RELATIVE_BASE`` or the current time, and ``"preference"`` when
        inferred from a preference setting like ``PREFER_DAY_OF_MONTH``."""

        self.base = base
        """Reference datetime that relative or incomplete inputs were
        resolved against, or ``None``."""

        self.meaning = meaning
        """Human-readable explanation of the interval for inputs that are
        only a weekday or a relative expression, or ``None``."""

        self.reason = reason
        """Why the interval could not be determined, e.g. because the input
        could not be parsed or the interval end overflows the supported
        datetime range, or ``None``."""

    def __getitem__(self, k: str) -> Any:
        if not hasattr(self, k):
            raise KeyError(k)
        return getattr(self, k)

    def __setitem__(self, k: str, v: Any) -> None:
        if not hasattr(self, k):
            raise KeyError(k)
        setattr(self, k, v)

    def __repr__(self) -> str:
        properties_text = ", ".join(
            f"{prop}={val!r}" for prop, val in self.__dict__.items()
        )

        return f"{self.__class__.__name__}({properties_text})"


def _precision(date_data: "DateData") -> str | None:
    parts = date_data.parts
    if "time" in parts or date_data.period in ("time", "part_of_day"):
        if date_data.time_parts:
            return date_data.time_parts[-1]
        if date_data.period == "part_of_day":
            return "hour"
        date_obj = date_data.date_obj
        assert date_obj is not None
        if date_obj.microsecond:
            return "microsecond"
        if date_obj.second:
            return "second"
        return "minute"
    for part in ("day", "month", "year"):
        if part in parts:
            return part
    return None


def _part_sources(
    date_data: "DateData", settings: Settings, precision: str
) -> dict[str, str]:
    input_parts = (
        date_data.parts if date_data.input_parts is None else date_data.input_parts
    )
    sources = {}
    for part in _DATE_PARTS:
        if part in input_parts or (part == "day" and date_data.weekday_only):
            source = PART_SOURCE_INPUT
        elif part == "day" and settings.PREFER_DAY_OF_MONTH != "current":
            source = PART_SOURCE_PREFERENCE
        elif part == "month" and settings.PREFER_MONTH_OF_YEAR != "current":
            source = PART_SOURCE_PREFERENCE
        else:
            source = PART_SOURCE_RELATIVE_BASE
        sources[part] = source
    if precision in _TIME_PARTS:
        for part in _TIME_PARTS[: _TIME_PARTS.index(precision) + 1]:
            if date_data.part_of_day is not None and not date_data.time_parts:
                # The time comes from the PARTS_OF_DAY setting.
                source = PART_SOURCE_PREFERENCE
            elif date_data.relative:
                source = (
                    PART_SOURCE_INPUT
                    if "time" in input_parts
                    else PART_SOURCE_RELATIVE_BASE
                )
            elif part in date_data.time_parts or "time" in input_parts:
                source = PART_SOURCE_INPUT
            else:
                source = PART_SOURCE_RELATIVE_BASE
            sources[part] = source
    return sources


def _meaning(date_data: "DateData", precision: str) -> str | None:
    base = date_data.base
    if isinstance(base, datetime):
        base_text = base.isoformat()
    else:
        base_text = "the current time at parse"
    if date_data.relative:
        return (
            f"relative expression resolved against base {base_text}; "
            f"the interval covers the {precision} containing the resolved moment"
        )
    if date_data.weekday_only:
        return (
            f"weekday-only input resolved against base {base_text}; "
            f"the interval covers the {precision} of the resolved date"
        )
    return None


def _truncate(date_obj: datetime, precision: str) -> datetime:
    if precision == "year":
        return date_obj.replace(
            month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    if precision == "month":
        return date_obj.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if precision == "day":
        return date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
    if precision == "hour":
        return date_obj.replace(minute=0, second=0, microsecond=0)
    if precision == "minute":
        return date_obj.replace(second=0, microsecond=0)
    if precision == "second":
        return date_obj.replace(microsecond=0)
    if precision == "millisecond":
        return date_obj.replace(microsecond=date_obj.microsecond // 1000 * 1000)
    return date_obj


def _add_precision(start: datetime, precision: str) -> datetime:
    if precision == "year":
        return start.replace(year=start.year + 1)
    if precision == "month":
        years, month = divmod(start.month, 12)
        return start.replace(year=start.year + years, month=month + 1)
    if precision == "day":
        return start + timedelta(days=1)
    if precision == "hour":
        return start + timedelta(hours=1)
    if precision == "minute":
        return start + timedelta(minutes=1)
    if precision == "second":
        return start + timedelta(seconds=1)
    if precision == "millisecond":
        return start + timedelta(milliseconds=1)
    return start + timedelta(microseconds=1)


def _localize(date_obj: datetime, tz: tzinfo | None) -> datetime:
    if tz is None:
        return date_obj
    localize = getattr(tz, "localize", None)
    if localize is not None:
        try:
            return localize(date_obj, is_dst=None)
        except InvalidTimeError:
            # Ambiguous or nonexistent local time (e.g. a midnight skipped by
            # a DST transition): fall back to the default interpretation.
            return localize(date_obj)
    return date_obj.replace(tzinfo=tz)


def interval_from_date_data(
    date_data: "DateData", settings: Settings | None = None
) -> DateInterval:
    """Return the :class:`DateInterval` that *date_data* determines.

    *settings* are the settings the date was parsed with, used to tell apart
    parts completed from ``RELATIVE_BASE`` and parts inferred from
    preferences. The default settings are assumed when not given.
    """
    if settings is None:
        settings = Settings()
    date_obj = date_data.date_obj
    if date_obj is None:
        return DateInterval(reason="no date could be parsed from the input")
    precision = _precision(date_data)
    if precision is None:
        return DateInterval(
            reason="the precision of the parsed date could not be determined",
        )
    tz = date_obj.tzinfo
    start = _truncate(date_obj.replace(tzinfo=None), precision)
    part_sources = _part_sources(date_data, settings, precision)
    meaning = _meaning(date_data, precision)
    try:
        end = _add_precision(start, precision)
    except (OverflowError, ValueError):
        return DateInterval(
            start=_localize(start, tz),
            precision=precision,
            part_sources=part_sources,
            base=date_data.base,
            meaning=meaning,
            reason="the interval end overflows the supported datetime range",
        )
    return DateInterval(
        start=_localize(start, tz),
        end=_localize(end, tz),
        precision=precision,
        part_sources=part_sources,
        base=date_data.base,
        meaning=meaning,
    )

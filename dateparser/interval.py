"""Interval result mode.

This module interprets a parsed date as the smallest calendar interval that
is consistent with the parts of the date string that were explicitly given,
instead of as a single point in time. A string like ``"March 2025"`` does
not refer to a specific day, so while :class:`~dateparser.date.DateData`
must pick one (from ``RELATIVE_BASE`` or the ``PREFER_*`` settings), the
interval mode reports ``[2025-03-01, 2025-04-01)`` with ``precision``
``"month"``.

Interval boundaries follow the calendar: a month is as long as that month
is (February 2024 has 29 days, not 30), and a day in a time zone that goes
through a DST transition is 23 or 25 hours long, not 24.
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from pytz import AmbiguousTimeError, NonExistentTimeError

from dateparser._parts_of_day import PartOfDay
from dateparser.utils import _now

if TYPE_CHECKING:
    from dateparser.conf import Settings
    from dateparser.date import DateData

#: Precisions that the interval mode can report, from the coarsest to the
#: finest supported one. ``"part_of_day"`` stands apart: its bounds come
#: from the ``PARTS_OF_DAY`` setting, not from calendar units.
PRECISIONS = (
    "year",
    "month",
    "week",
    "day",
    "hour",
    "minute",
    "second",
    "millisecond",
    "microsecond",
)

_SUBDAY_PRECISIONS = ("hour", "minute", "second", "millisecond", "microsecond")

#: Origins that :attr:`IntervalData.sources` assigns to each date part:
#: ``"input"`` (given by the date string), ``"relative_base"`` (taken from
#: the ``RELATIVE_BASE`` setting or the current time) and ``"preference"``
#: (inferred from a ``PREFER_*`` setting).
SOURCE_INPUT = "input"
SOURCE_RELATIVE_BASE = "relative_base"
SOURCE_PREFERENCE = "preference"


class _UndeterminableInterval(Exception):
    """A boundary of the interval cannot be determined, e.g. it falls on a
    wall clock time that a DST transition makes ambiguous or nonexistent."""


class IntervalData:
    """
    The smallest calendar interval consistent with a parsed date string,
    together with the precision and provenance of each date part.

    It can be accessed with square brackets like a dict object.

    .. versionadded:: VERSION
    """

    def __init__(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        precision: str | None = None,
        sources: dict[str, str] | None = None,
        basis: str | None = None,
        relative_base: datetime | None = None,
        error: str | None = None,
    ) -> None:
        self.start = start
        """Start of the interval, inclusive. ``None`` when it cannot be
        determined; see :attr:`error`."""
        self.end = end
        """End of the interval, exclusive. ``None`` when it cannot be
        determined or is not representable; see :attr:`error`."""
        self.precision = precision
        """Finest calendar unit the date string determines: one of
        ``"year"``, ``"month"``, ``"week"``, ``"day"``, ``"hour"``,
        ``"minute"``, ``"second"``, ``"millisecond"``, ``"microsecond"``
        or ``"part_of_day"``."""
        self.sources = sources or {}
        """Origin of each part of the parsed date: ``"input"`` when the
        string gave it, ``"relative_base"`` when it was filled from the
        ``RELATIVE_BASE`` setting (or the current time) and ``"preference"``
        when a ``PREFER_*`` setting inferred it."""
        self.basis = basis
        """How the date was established when it does not name its parts
        directly: ``"weekday"`` for a bare weekday (e.g. ``"Friday"``,
        resolved against :attr:`relative_base` following
        ``PREFER_DATES_FROM``) and ``"relative"`` for a relative expression
        (e.g. ``"3 days ago"``, resolved against :attr:`relative_base`).
        ``None`` otherwise."""
        self.relative_base = relative_base
        """The reference datetime used to fill or shift parts that the
        string did not give, when one was used."""
        self.error = error
        """Why the interval could not be fully determined (e.g. an
        overflowing or ambiguous boundary), or ``None``."""

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


def interval_from_date_data(
    date_data: "DateData", settings: "Settings"
) -> IntervalData:
    """Return the :class:`IntervalData` for an already parsed *date_data*.

    The :attr:`~dateparser.date.DateData.date_obj` selection is not affected
    by this computation in any way.
    """
    date_obj = date_data.date_obj
    if not isinstance(date_obj, datetime):
        return IntervalData(error="no date could be parsed from the string")

    precision = _precision(date_data)
    sources = _sources(date_data, settings)
    basis = date_data._basis
    relative_base = date_data._relative_base
    if relative_base is None and (
        basis is not None or SOURCE_RELATIVE_BASE in sources.values()
    ):
        relative_base = settings.RELATIVE_BASE or _now(settings)

    try:
        start = _boundary(date_obj, precision, date_data, settings, is_start=True)
    except _UndeterminableInterval as exc:
        return IntervalData(
            precision=precision,
            sources=sources,
            basis=basis,
            relative_base=relative_base,
            error=f"interval start cannot be determined: {exc}",
        )
    except (OverflowError, ValueError) as exc:
        return IntervalData(
            precision=precision,
            sources=sources,
            basis=basis,
            relative_base=relative_base,
            error=f"interval start overflows the supported datetime range: {exc}",
        )

    end = None
    error = None
    try:
        end = _boundary(date_obj, precision, date_data, settings, is_start=False)
    except _UndeterminableInterval as exc:
        error = f"interval end cannot be determined: {exc}"
    except (OverflowError, ValueError) as exc:
        error = f"interval end overflows the supported datetime range: {exc}"

    return IntervalData(
        start=start,
        end=end,
        precision=precision,
        sources=sources,
        basis=basis,
        relative_base=relative_base,
        error=error,
    )


def _precision(date_data: "DateData") -> str:
    resolution = date_data._resolution
    if date_data.period == "part_of_day" or date_data._part_of_day_sets_time:
        return "part_of_day"
    if "time" in date_data.parts:
        if resolution in _SUBDAY_PRECISIONS:
            return resolution
        # Fallback for parses that recorded no resolution: derive it from
        # the finest non-zero time field of the parsed date.
        date_obj = date_data.date_obj
        assert isinstance(date_obj, datetime)
        if date_obj.microsecond % 1000:
            return "microsecond"
        if date_obj.microsecond:
            return "millisecond"
        if date_obj.second:
            return "second"
        if date_obj.minute:
            return "minute"
        return "hour"
    if resolution is not None:
        # A relative expression (e.g. "2 months ago") records the smallest
        # unit it named as its resolution.
        return resolution
    if date_data.period in PRECISIONS:
        return date_data.period
    return "day"


def _sources(date_data: "DateData", settings: "Settings") -> dict[str, str]:
    parts = set(date_data.parts)
    missing = set(date_data._missing_parts)
    basis = date_data._basis

    if basis == "relative":
        # A relative expression (e.g. "3 days ago") derives every date part
        # from the reference datetime; only an explicit clock time in the
        # string itself (e.g. "1 day ago, 2 PM") counts as input.
        sources = {part: SOURCE_RELATIVE_BASE for part in ("year", "month", "day")}
        if "time" in parts:
            if date_data._part_of_day_sets_time:
                sources["time"] = SOURCE_PREFERENCE
            elif date_data._time_from_input:
                sources["time"] = SOURCE_INPUT
            else:
                sources["time"] = SOURCE_RELATIVE_BASE
        return sources

    if basis == "weekday":
        # A bare weekday (e.g. "Friday") takes the year and month from the
        # reference datetime and resolves the day from it following
        # PREFER_DATES_FROM.
        sources = {"year": SOURCE_RELATIVE_BASE, "month": SOURCE_RELATIVE_BASE}
        sources["day"] = (
            SOURCE_PREFERENCE
            if settings.PREFER_DATES_FROM != "current_period"
            else SOURCE_RELATIVE_BASE
        )
        if "time" in parts:
            sources["time"] = SOURCE_INPUT
        return sources

    sources = {}
    for part in ("year", "month", "day"):
        if part in parts and part not in missing:
            sources[part] = SOURCE_INPUT
        elif _preference_fills(part, settings):
            sources[part] = SOURCE_PREFERENCE
        else:
            sources[part] = SOURCE_RELATIVE_BASE
    if "time" in parts:
        sources["time"] = (
            SOURCE_PREFERENCE if date_data._part_of_day_sets_time else SOURCE_INPUT
        )
    return sources


def _preference_fills(part: str, settings: "Settings") -> bool:
    """Return whether a ``PREFER_*`` setting decides the value of *part*
    when the date string does not give it."""
    if part == "year":
        return settings.PREFER_DATES_FROM != "current_period"
    if part == "month":
        return settings.PREFER_MONTH_OF_YEAR != "current"
    return settings.PREFER_DAY_OF_MONTH != "current"


def _boundary(
    date_obj: datetime,
    precision: str,
    date_data: "DateData",
    settings: "Settings",
    is_start: bool,
) -> datetime:
    tz = date_obj.tzinfo
    naive = date_obj.replace(tzinfo=None)
    if precision == "part_of_day":
        start, end = _part_of_day_bounds(naive, date_data, settings)
        bound = start if is_start else end
    elif is_start:
        bound = _naive_start(naive, precision)
    else:
        bound = _naive_end(naive, precision)
    return _localize(bound, tz)


def _naive_start(dt: datetime, precision: str) -> datetime:
    """Return the naive start of the *precision*-sized calendar cell that
    contains *dt*."""
    if precision == "year":
        return dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    if precision == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if precision in ("week", "day"):
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if precision == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    if precision == "minute":
        return dt.replace(second=0, microsecond=0)
    if precision == "second":
        return dt.replace(microsecond=0)
    if precision == "millisecond":
        return dt.replace(microsecond=dt.microsecond // 1000 * 1000)
    if precision == "microsecond":
        return dt
    raise ValueError(f"Unsupported precision: {precision}")


def _naive_end(dt: datetime, precision: str) -> datetime:
    """Return the naive end (exclusive) of the *precision*-sized calendar
    cell that contains *dt*, following calendar boundaries: a month is as
    long as that month is, never a fixed number of days."""
    start = _naive_start(dt, precision)
    if precision == "year":
        return start.replace(year=start.year + 1)
    if precision == "month":
        if start.month == 12:
            return start.replace(year=start.year + 1, month=1)
        return start.replace(month=start.month + 1)
    if precision == "week":
        # The seven-day span that begins at the start of the resolved day.
        return start + timedelta(days=7)
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
    if precision == "microsecond":
        return dt + timedelta(microseconds=1)
    raise ValueError(f"Unsupported precision: {precision}")


def _part_of_day_bounds(
    dt: datetime, date_data: "DateData", settings: "Settings"
) -> tuple[datetime, datetime]:
    """Return the naive bounds of the part of the day of *dt*, from its
    configured time to the time of the next one (wrapping past midnight)."""
    part = date_data.part_of_day
    if part is None:
        raise _UndeterminableInterval("the part of the day is unknown")
    parts_of_day = settings.PARTS_OF_DAY
    times = sorted(getattr(parts_of_day, p.value) for p in PartOfDay)
    start_time = getattr(parts_of_day, part.value)
    index = times.index(start_time)
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    start = day.replace(
        hour=start_time.hour,
        minute=start_time.minute,
        second=start_time.second,
        microsecond=start_time.microsecond,
    )
    if index + 1 < len(times):
        end_time = times[index + 1]
        end_day = day
    else:
        end_time = times[0]
        end_day = day + timedelta(days=1)
    end = end_day.replace(
        hour=end_time.hour,
        minute=end_time.minute,
        second=end_time.second,
        microsecond=end_time.microsecond,
    )
    return start, end


def _localize(naive: datetime, tz: Any) -> datetime:
    """Attach *tz* to the naive boundary *naive*, keeping the wall clock.

    Localizing each boundary (instead of adding timedeltas to an aware
    datetime) is what makes a day 23 or 25 hours long across a DST
    transition. A boundary that the transition makes ambiguous or
    nonexistent raises :class:`_UndeterminableInterval`.
    """
    if tz is None:
        return naive
    localize = getattr(tz, "localize", None)
    if localize is not None:
        try:
            return localize(naive, is_dst=None)
        except TypeError:
            # A localize() that takes no is_dst argument.
            return localize(naive)
        except AmbiguousTimeError as exc:
            raise _UndeterminableInterval(
                f"{exc} is ambiguous in time zone {tz} because of a DST"
                " transition"
            ) from exc
        except NonExistentTimeError as exc:
            raise _UndeterminableInterval(
                f"{exc} does not exist in time zone {tz} because of a DST"
                " transition"
            ) from exc
    return naive.replace(tzinfo=tz)

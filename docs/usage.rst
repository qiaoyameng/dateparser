.. _using-datedataparser:


Using DateDataParser
--------------------

:func:`dateparser.parse` uses a default parser which tries to detect language
every time it is called and is not the most efficient way while parsing dates
from the same source.

:class:`DateDataParser <dateparser.date.DateDataParser>` provides an alternate and efficient way
to control language detection behavior.

See :ref:`performance` for ways to make parsing faster.

:class:`dateparser.date.DateDataParser` can also be initialized with known languages:

    >>> ddp = DateDataParser(languages=['de', 'nl'])
    >>> ddp.get_date_data('vr jan 24, 2014 12:49')
    DateData(date_obj=datetime.datetime(2014, 1, 24, 12, 49), period='day', locale='nl', parts=('year', 'month', 'day', 'time'))
    >>> ddp.get_date_data('18.10.14 um 22:56 Uhr')
    DateData(date_obj=datetime.datetime(2014, 10, 18, 22, 56), period='day', locale='de', parts=('year', 'month', 'day', 'time'))
    >>> ddp.get_date_data('11 July 2012')
    DateData(date_obj=None, period='day', locale=None, parts=())


.. _interval-mode:


Interval result mode
--------------------

A date string like ``"March 2025"`` does not refer to a specific day, but
:attr:`~dateparser.date.DateData.date_obj` must pick one, which hides the
precision of the input. The interval result mode keeps it: it returns the
smallest calendar interval consistent with the parts of the string that
were explicitly given, through
:meth:`DateDataParser.get_interval_data <dateparser.date.DateDataParser.get_interval_data>`
or the top-level :func:`dateparser.parse_interval`:

    >>> dateparser.parse_interval('March 2025')
    IntervalData(start=datetime.datetime(2025, 3, 1, 0, 0), end=datetime.datetime(2025, 4, 1, 0, 0), precision='month', ...)

The interval follows calendar boundaries: a month is as long as that month
is (February 2024 has 29 days, not 30), and a day in a time zone that goes
through a DST transition is 23 or 25 hours long, not 24. Fractional seconds
keep the supported resolution, down to microseconds.

The returned :class:`~dateparser.interval.IntervalData` also reports:

- ``sources``: the origin of each date part — ``"input"`` when the string
  gave it, ``"relative_base"`` when it was filled from the ``RELATIVE_BASE``
  setting (or the current time) and ``"preference"`` when a ``PREFER_*``
  setting inferred it.
- ``basis`` and ``relative_base``: for strings that only name a weekday
  (``basis="weekday"``) or use a relative expression like ``"3 days ago"``
  (``basis="relative"``), the reference datetime the date was resolved
  against.
- ``error``: why a boundary could not be determined, e.g. an overflowing
  year or a wall clock time that a DST transition makes ambiguous or
  nonexistent.

This mode does not change the ``date_obj`` that :func:`dateparser.parse`
and :meth:`~dateparser.date.DateDataParser.get_date_data` return.

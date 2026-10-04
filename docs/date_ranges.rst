.. _parsing-date-ranges:


Parsing date ranges
-------------------

Reports and forms often describe a span with two explicit ends, e.g.
``"2025年3月3日至5日"`` or ``"30 Dec–2 Jan 2026"``. Use
:func:`dateparser.parse_date_range` (or
:meth:`DateDataParser.get_date_range_data <dateparser.date.DateDataParser.get_date_range_data>`)
for these expressions. Standalone dates still go through
:func:`dateparser.parse`.

Only explicit forms are accepted. The two ends share the year and month
they do not repeat. A cross-month or cross-year carry is applied only when
the text forces a unique one; otherwise the result is marked *ambiguous*
instead of silently choosing the nearest date.

    >>> from dateparser import parse_date_range
    >>> result = parse_date_range('2025年3月3日至5日', languages=['zh'])
    >>> result.start, result.end
    (datetime.datetime(2025, 3, 3, 0, 0), datetime.datetime(2025, 3, 5, 0, 0))
    >>> result.interval_start, result.interval_end
    (datetime.datetime(2025, 3, 3, 0, 0), datetime.datetime(2025, 3, 6, 0, 0))

The closed ends are available as ``start`` and ``end`` (the original two
date parts), while ``interval_start`` and ``interval_end`` express the same
span as a standardized half-open interval ``[start, end)``. A date-only end
advances to midnight of the following day (``2025-03-05`` becomes the
exclusive ``2025-03-06``); an end that carries a time of its own is kept
exactly as written.

    >>> result = parse_date_range('30 Dec to 2 Jan 2026', languages=['en'])
    >>> result.start, result.end
    (datetime.datetime(2025, 12, 30, 0, 0), datetime.datetime(2026, 1, 2, 0, 0))

When the range cannot be read uniquely, an object with ``ambiguous=True`` is
returned together with the remaining ``candidates``::

    >>> result = parse_date_range('12月31日至1月2日', languages=['zh'])
    >>> result.ambiguous
    True

Impossible dates (e.g. ``2025年2月30日``) are not ranges and return ``None``.

Range connectors are recognized only in date grammar context, so the hyphens
inside an ISO date (``2025-03-03``) are never treated as range connectors.
If the two ends give inconsistent time zone information (one offset-aware,
the other naive), :class:`ValueError` is raised rather than assuming a time
zone; two aware ends may be normalized with the ``TO_TIMEZONE`` setting.

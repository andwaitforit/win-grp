"""Broadcast-week helpers. Broadcast weeks run Monday through Sunday."""

from __future__ import annotations

import datetime as dt


def broadcast_week(d: dt.date) -> dt.date:
    """Monday of the broadcast week containing d."""
    return d - dt.timedelta(days=d.weekday())


def allocate_by_week(start: dt.date, end: dt.date, cents: int, spots: int) -> list[tuple[dt.date, int, int]]:
    """Split a flight's dollars and spots across broadcast weeks by day count.

    Returns (week, cents, spots) per week. Rounding remainders go to the last
    week so totals are preserved exactly.
    """
    if end < start:
        raise ValueError(f"flight ends before it starts: {start} > {end}")
    days = (end - start).days + 1
    weeks: dict[dt.date, int] = {}
    d = start
    while d <= end:
        wk = broadcast_week(d)
        weeks[wk] = weeks.get(wk, 0) + 1
        d += dt.timedelta(days=1)

    out = []
    left_c, left_s = cents, spots
    items = sorted(weeks.items())
    for i, (wk, n) in enumerate(items):
        if i == len(items) - 1:
            c, s = left_c, left_s
        else:
            c = round(cents * n / days)
            s = round(spots * n / days)
        left_c -= c
        left_s -= s
        out.append((wk, c, s))
    return out


def overlaps(week: dt.date, window: tuple[dt.date, dt.date]) -> bool:
    """True if any day of the broadcast week falls inside the window."""
    return week <= window[1] and week + dt.timedelta(days=6) >= window[0]

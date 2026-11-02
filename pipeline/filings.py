"""Load hand-entered political-file buys and turn them into weekly rows.

buys.csv has one row per line of a filing (an order, a revised order, or an
invoice). Rows with the same filing_id come from the same document.

Double-count rules (CLAUDE.md: never double-count a revised order):
- A revision supersedes the earlier filing it names, entirely.
- An invoice supersedes the order it names, but only for the broadcast weeks
  the invoice covers. Order weeks without an invoice yet still count.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .race import Race
from .weeks import allocate_by_week

DOC_TYPES = ("order", "revision", "invoice")
REQUIRED_COLUMNS = (
    "filing_id", "doc_type", "filed_date", "buyer", "station",
    "flight_start", "flight_end", "spots", "gross_usd", "supersedes", "source_url",
)


class DataError(ValueError):
    """A problem in the input files, reported with file and row number."""


@dataclass(frozen=True)
class Line:
    row: int
    filing_id: str
    doc_type: str
    filed_date: dt.date
    buyer: str
    station: str
    market: str
    flight_start: dt.date
    flight_end: dt.date
    spots: int
    gross_cents: int
    supersedes: str
    source_url: str
    notes: str


@dataclass(frozen=True)
class Filing:
    id: str
    doc_type: str
    filed_date: dt.date
    buyer: str
    station: str
    market: str
    supersedes: str
    source_url: str
    lines: tuple[Line, ...]

    @property
    def gross_cents(self) -> int:
        return sum(l.gross_cents for l in self.lines)

    @property
    def flight(self) -> tuple[dt.date, dt.date]:
        return min(l.flight_start for l in self.lines), max(l.flight_end for l in self.lines)


@dataclass(frozen=True)
class WeekRow:
    filing_id: str
    doc_type: str
    buyer: str
    station: str
    market: str
    week: dt.date
    gross_cents: int
    spots: int
    status: str  # "aired" | "airing" | "booked"
    confirmed: bool  # True when the week is covered by an invoice
    source_url: str


def _date(v: str, field: str, where: str) -> dt.date:
    try:
        return dt.date.fromisoformat(v.strip())
    except ValueError:
        raise DataError(f"{where}: {field} must be YYYY-MM-DD, got {v!r}") from None


def _cents(v: str, where: str) -> int:
    try:
        d = Decimal(v.replace("$", "").replace(",", "").strip())
    except InvalidOperation:
        raise DataError(f"{where}: gross_usd must be a number, got {v!r}") from None
    if d < 0:
        raise DataError(f"{where}: gross_usd can't be negative")
    return int((d * 100).quantize(Decimal(1)))


def load_lines(path: str | Path, race: Race, require_source: bool = True) -> list[Line]:
    path = Path(path)
    lines: list[Line] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise DataError(f"{path.name}: missing columns {', '.join(missing)}")
        for i, r in enumerate(reader, start=2):
            where = f"{path.name} row {i}"
            if not any((v or "").strip() for v in r.values()):
                continue
            doc_type = r["doc_type"].strip().lower()
            if doc_type not in DOC_TYPES:
                raise DataError(f"{where}: doc_type must be one of {', '.join(DOC_TYPES)}")
            buyer = race.resolve_buyer(r["buyer"])
            if buyer is None:
                raise DataError(
                    f"{where}: unknown buyer {r['buyer']!r}. Add it to outside_groups "
                    "(or a candidate's opif_purchaser_aliases) in the race config."
                )
            station = race.stations.get(r["station"].strip().upper())
            if station is None:
                raise DataError(f"{where}: unknown station {r['station']!r}. Add it to the race config.")
            source = (r["source_url"] or "").strip()
            if require_source and not source:
                raise DataError(f"{where}: source_url is required so every number traces to its filing")
            try:
                spots = int((r["spots"] or "0").strip())
            except ValueError:
                raise DataError(f"{where}: spots must be a whole number") from None
            start = _date(r["flight_start"], "flight_start", where)
            end = _date(r["flight_end"], "flight_end", where)
            if end < start:
                raise DataError(f"{where}: flight_end is before flight_start")
            lines.append(Line(
                row=i,
                filing_id=r["filing_id"].strip(),
                doc_type=doc_type,
                filed_date=_date(r["filed_date"], "filed_date", where),
                buyer=buyer.key,
                station=station.call_sign,
                market=station.market,
                flight_start=start,
                flight_end=end,
                spots=spots,
                gross_cents=_cents(r["gross_usd"], where),
                supersedes=(r["supersedes"] or "").strip(),
                source_url=source,
                notes=(r.get("notes") or "").strip(),
            ))
    return lines


def group_filings(lines: list[Line]) -> dict[str, Filing]:
    by_id: dict[str, list[Line]] = {}
    for l in lines:
        by_id.setdefault(l.filing_id, []).append(l)
    filings = {}
    for fid, ls in by_id.items():
        first = ls[0]
        for l in ls[1:]:
            for attr in ("doc_type", "filed_date", "buyer", "station", "supersedes"):
                if getattr(l, attr) != getattr(first, attr):
                    raise DataError(
                        f"buys.csv row {l.row}: filing {fid} has a different {attr} than row {first.row}; "
                        "every line of one filing must share it"
                    )
        filings[fid] = Filing(
            id=fid, doc_type=first.doc_type, filed_date=first.filed_date, buyer=first.buyer,
            station=first.station, market=first.market, supersedes=first.supersedes,
            source_url=first.source_url, lines=tuple(ls),
        )
    for f in filings.values():
        if f.doc_type == "revision" and not f.supersedes:
            raise DataError(f"filing {f.id}: a revision must name the filing it supersedes")
        if f.supersedes:
            prev = filings.get(f.supersedes)
            if prev is None:
                raise DataError(f"filing {f.id}: supersedes unknown filing {f.supersedes!r}")
            if (prev.buyer, prev.station) != (f.buyer, f.station):
                raise DataError(f"filing {f.id}: supersedes {prev.id}, which is a different buyer or station")
            if prev.doc_type == "invoice":
                raise DataError(f"filing {f.id}: can't supersede an invoice ({prev.id})")
    return filings


def weekly_rows(filings: dict[str, Filing], as_of: dt.date) -> list[WeekRow]:
    revised = {f.supersedes for f in filings.values() if f.doc_type == "revision"}
    for f in filings.values():
        if f.doc_type == "invoice" and f.supersedes in revised:
            raise DataError(
                f"invoice {f.id} names {f.supersedes}, which was later revised; "
                "point it at the latest revision instead"
            )
    live = [f for f in filings.values() if f.id not in revised]

    alloc: list[tuple[Filing, dt.date, int, int]] = []
    for f in live:
        for l in f.lines:
            for wk, c, s in allocate_by_week(l.flight_start, l.flight_end, l.gross_cents, l.spots):
                alloc.append((f, wk, c, s))

    invoiced: set[tuple[str, dt.date]] = {
        (f.supersedes, wk) for f, wk, _, _ in alloc if f.doc_type == "invoice" and f.supersedes
    }
    rows = []
    for f, wk, c, s in alloc:
        if f.doc_type != "invoice" and (f.id, wk) in invoiced:
            continue
        if f.doc_type == "invoice" or wk + dt.timedelta(days=6) < as_of:
            status = "aired"
        elif wk <= as_of:
            status = "airing"
        else:
            status = "booked"
        rows.append(WeekRow(
            filing_id=f.id, doc_type=f.doc_type, buyer=f.buyer, station=f.station, market=f.market,
            week=wk, gross_cents=c, spots=s, status=status, confirmed=f.doc_type == "invoice",
            source_url=f.source_url,
        ))
    rows.sort(key=lambda r: (r.week, r.buyer, r.station))
    return rows

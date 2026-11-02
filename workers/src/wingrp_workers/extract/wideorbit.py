"""Parser for WideOrbit traffic-system contracts (political orders).

Input is `pdftotext -layout` output (see `pdftext.py`). Seen on Nexstar
stations (WWLP/EWLP) for 2026; footer reads "powered by WideOrbit".

Layout, per page:
  header block   station, contract #, advertiser, contract dates, demo, ...
  line items     `N  12 WWLP 10/19/26 10/25/26 Thu 8-9p   8-9p   :30   NM   1   $300.00`
                 followed by one or more week rows:
                 `Week: 10/19/26 10/25/26 ---1---   1   $300.00`
                 (a line's week rows can continue onto the next page)
last page:       `Totals  172  $20,440.00` and a broadcast-month summary with
                 gross, agency commission, and net.

Contracts are *orders* (planned spots), not invoices. Money is integer cents.
`issues` lists every reconciliation failure; a contract with issues belongs in
the review queue, not in estimates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

FORMAT = "wideorbit_contract"
PARSER_VERSION = "2"  # bump when parsing changes, so stored rows can be re-parsed

_D = r"\d\d/\d\d/\d\d"
_M = r"\$([\d,]+\.\d\d)"
_IMP = r"(?:\s+([\d,]+(?:\.\d+)?))?"  # optional "Imp (000)" column (some contracts carry it)
# Action is blank on lines a revision left unchanged.
_LINE_RE = re.compile(rf"^\s*([NED]?)\s+(\d+)\s+(\S+)\s+({_D})\s+({_D})\s+(.*\S)\s*$")
_WEEK_RE = re.compile(rf"^\s*Week:\s+({_D})\s+({_D})\s+([A-Z0-9-]{{7}})\s+(\d+)\s+{_M}{_IMP}\s*$")
_PERIOD_RE = re.compile(
    rf"^\s*(?:({_D})\s*-\s*({_D})|Totals)\s+(\d+)\s+{_M}\s+\({_M}\)\s+{_M}\s*$"
)
_LINE_TOTAL_RE = re.compile(rf"^\s*Totals(?:\s+([\d,]+\.\d))?\s+(\d+)\s+{_M}\s*$")
_LENGTH_RE = re.compile(r"^(?::\d{2}|\d{1,2}:\d{2})$")
_CONTRACT_NO_RE = re.compile(r"(\d{6,})\s*/\s*(\d{1,3}(?!\d))?")


@dataclass(frozen=True)
class Week:
    start: date
    end: date
    weekdays: str  # 7 chars Mon..Sun, e.g. "-TWTF--" or "-----3-" (spots per day)
    spots: int
    rate_cents: int
    imp_000: float | None = None  # station's impressions estimate per spot, thousands


@dataclass
class Line:
    line_no: int
    action: str  # N new, E edited, D deleted, U unchanged (blank on a revision)
    channel: str
    start: date
    end: date
    description: str
    time: str | None
    days: str | None
    length_s: int | None
    spot_type: str
    spots: int
    amount_cents: int
    page: int  # 1-based PDF page the line starts on
    weeks: list[Week] = field(default_factory=list)


@dataclass(frozen=True)
class Period:
    """Broadcast-month subtotal from the summary block (start/end None = Totals row)."""

    start: date | None
    end: date | None
    spots: int
    gross_cents: int
    commission_cents: int
    net_cents: int


@dataclass
class Contract:
    station: str | None = None
    contract_number: str | None = None
    revision: int | None = None
    alt_order: str | None = None
    advertiser: str | None = None
    product: str | None = None
    estimate: str | None = None
    demographic: str | None = None
    bill_to: list[str] = field(default_factory=list)  # agency/payer name then address
    contract_start: date | None = None
    contract_end: date | None = None
    print_date: date | None = None
    original_date: date | None = None
    revision_date: date | None = None
    lines: list[Line] = field(default_factory=list)
    periods: list[Period] = field(default_factory=list)
    total_spots: int | None = None
    gross_cents: int | None = None
    commission_cents: int | None = None
    net_cents: int | None = None
    impressions_000: float | None = None  # line-items Totals row, when the Imp column is present
    pages: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def reconciled(self) -> bool:
        return not self.issues

    def weekly(self) -> list[dict]:
        """One row per (line, broadcast week): the grain GRP estimation works at."""
        return [
            {
                "line_no": ln.line_no,
                "channel": ln.channel,
                "week_start": w.start,
                "description": ln.description,
                "time": ln.time,
                "spots": w.spots,
                "gross_cents": w.spots * w.rate_cents,
                "impressions_000": w.spots * w.imp_000 if w.imp_000 is not None else None,
                "page": ln.page,
            }
            for ln in self.lines
            if ln.action != "D"
            for w in ln.weeks
        ]


def is_wideorbit_contract(text: str) -> bool:
    return "powered by WideOrbit" in text and "Contract Agreement Between" in text


def _date(s: str) -> date:
    m, d, y = s.split("/")
    return date(2000 + int(y), int(m), int(d))


def _cents(s: str) -> int:
    whole, frac = s.replace(",", "").split(".")
    return int(whole) * 100 + int(frac)


def _below(lines: list[str], label: str) -> str | None:
    """Text directly under a header label, in the label's column.

    The label must stand alone ("Product" matches, "Product 1/2" does not).
    """
    pat = re.compile(rf"(?<!\S){re.escape(label)}(?!\S| \d)")
    for i, ln in enumerate(lines[:-1]):
        if m := pat.search(ln):
            seg = lines[i + 1][max(0, m.start() - 2) :].strip()
            return re.split(r"\s{3,}", seg)[0] if seg else None
    return None


def _parse_header(page1: str, k: Contract) -> None:
    lines = page1.splitlines()
    if m := re.search(rf"Print Date\s+({_D})", page1):
        k.print_date = _date(m.group(1))
    for i, ln in enumerate(lines[:-1]):
        if ln.strip() == "CONTRACT" and lines[i + 1].strip():
            k.station = lines[i + 1].split()[0]
        if "Contract / Revision" in ln and (m := _CONTRACT_NO_RE.search(lines[i + 1])):
            k.contract_number = m.group(1)
            k.revision = int(m.group(2)) if m.group(2) else None
        if "Original Date / Revision" in ln:
            ds = re.findall(_D, lines[i + 1])
            if ds:
                k.original_date = _date(ds[0])
                k.revision_date = _date(ds[-1]) if len(ds) > 1 else None
        if ln.strip().startswith("And:"):
            for nxt in lines[i + 1 :]:
                if nxt.lstrip().startswith("* Line"):
                    break
                left = nxt[:60].strip()
                if left:
                    k.bill_to.append(left)
    k.alt_order = _below(lines, "Alt Order #")
    # The original date can run into the advertiser cell with no column gap.
    adv = _below(lines, "Advertiser")
    k.advertiser = re.sub(rf"\s+{_D}$", "", adv) if adv else None
    k.product = _below(lines, "Product")
    k.estimate = _below(lines, "Estimate #")
    k.demographic = _below(lines, "Demographic")
    if m := re.search(rf"({_D})\s*-\s*({_D})", page1):
        k.contract_start, k.contract_end = _date(m.group(1)), _date(m.group(2))


def _parse_line(m: re.Match[str], page: int) -> Line:
    action, no, ch, start, end, rest = m.groups()
    toks = re.split(r"\s{2,}", rest)
    amount = toks.pop()
    spots = toks.pop()
    spot_type = toks.pop()
    # Optional Spots/Week and Rate columns sit between Length and Type; they are
    # blank on summary lines (the week rows carry them), so find Length by shape.
    li = max((i for i, t in enumerate(toks) if _LENGTH_RE.match(t)), default=None)
    head = toks[:li] if li is not None else toks
    length = toks[li] if li is not None else None
    length_s = None
    if length:
        mm, _, ss = length.rpartition(":")
        length_s = int(mm or 0) * 60 + int(ss)
    return Line(
        line_no=int(no),
        action=action or "U",
        channel=ch,
        start=_date(start),
        end=_date(end),
        description=head[0] if head else "",
        time=head[1] if len(head) > 1 else None,
        days=" ".join(head[2:]) or None,
        length_s=length_s,
        spot_type=spot_type,
        spots=int(spots),
        amount_cents=_cents(amount.lstrip("$")),
        page=page,
    )


def parse_contract(text: str) -> Contract:
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    k = Contract(pages=len(pages))
    if not is_wideorbit_contract(text):
        k.issues.append("not a WideOrbit contract")
        return k
    _parse_header(pages[0], k)

    current: Line | None = None
    line_total: tuple[int, int] | None = None
    for pno, page in enumerate(pages, start=1):
        for raw in page.splitlines():
            if m := _PERIOD_RE.match(raw):
                start, end, spots, gross, comm, net = m.groups()
                p = Period(
                    _date(start) if start else None,
                    _date(end) if end else None,
                    int(spots), _cents(gross), _cents(comm), _cents(net),
                )
                if start:
                    k.periods.append(p)
                else:
                    k.total_spots, k.gross_cents = p.spots, p.gross_cents
                    k.commission_cents, k.net_cents = p.commission_cents, p.net_cents
            elif m := _LINE_TOTAL_RE.match(raw):
                imp, spots, gross = m.groups()
                line_total = (int(spots), _cents(gross))
                if imp:
                    k.impressions_000 = float(imp.replace(",", ""))
            elif m := _LINE_RE.match(raw):
                try:
                    current = _parse_line(m, pno)
                except (IndexError, ValueError) as e:
                    k.issues.append(f"page {pno}: unparsed line item {raw.strip()[:60]!r} ({e})")
                    current = None
                    continue
                k.lines.append(current)
            elif m := _WEEK_RE.match(raw):
                if current is None:
                    k.issues.append(f"page {pno}: week row with no line item")
                    continue
                s, e, days, spots, rate, imp = m.groups()
                current.weeks.append(Week(
                    _date(s), _date(e), days, int(spots), _cents(rate),
                    float(imp.replace(",", "")) if imp else None,
                ))  # fmt: skip

    _reconcile(k, line_total)
    return k


def _reconcile(k: Contract, line_total: tuple[int, int] | None) -> None:
    for f in ("station", "contract_number", "advertiser", "contract_start"):
        if getattr(k, f) is None:
            k.issues.append(f"header: {f} not found")
    if k.gross_cents is None:
        k.issues.append("summary Totals row not found")
        return
    if k.gross_cents - k.commission_cents != k.net_cents:
        k.issues.append("gross - commission != net")
    live = [ln for ln in k.lines if ln.action != "D"]
    spots, gross = sum(ln.spots for ln in live), sum(ln.amount_cents for ln in live)
    if (spots, gross) != (k.total_spots, k.gross_cents):
        k.issues.append(f"line items sum to {spots} spots / {gross}c, totals say {k.total_spots} / {k.gross_cents}c")
    if line_total and line_total != (k.total_spots, k.gross_cents):
        k.issues.append("line-item Totals row disagrees with summary Totals")
    if k.periods and (
        sum(p.spots for p in k.periods) != k.total_spots
        or sum(p.gross_cents for p in k.periods) != k.gross_cents
    ):
        k.issues.append("period subtotals do not sum to Totals")
    if k.impressions_000 is not None:
        imp = sum(w.spots * (w.imp_000 or 0) for ln in live for w in ln.weeks)
        # Per-spot impressions are printed to 0.1; allow that rounding.
        if abs(imp - k.impressions_000) > 0.05 * max(1, k.total_spots or 0):
            k.issues.append(f"week impressions sum to {imp:.1f}k, Totals say {k.impressions_000}k")
    for ln in live:
        ws, wg = sum(w.spots for w in ln.weeks), sum(w.spots * w.rate_cents for w in ln.weeks)
        if (ws, wg) != (ln.spots, ln.amount_cents):
            k.issues.append(f"line {ln.line_no}: weeks sum to {ws} spots / {wg}c, line says {ln.spots} / {ln.amount_cents}c")


def latest_versions(contracts: list[Contract]) -> list[Contract]:
    """Keep one version per (station, contract #): the latest revision/print date.

    Stations re-upload a contract when it changes (e.g. "Updated_..." files).
    Counting both would double-count spend. Ties keep the later list entry, so
    pass contracts in upload order.
    """
    best: dict[tuple, Contract] = {}
    for k in contracts:
        key = (k.station, k.contract_number)
        rank = (k.revision or 0, k.revision_date or k.original_date or date.min, k.print_date or date.min)
        cur = best.get(key)
        if cur is None or rank >= (
            cur.revision or 0, cur.revision_date or cur.original_date or date.min, cur.print_date or date.min
        ):
            best[key] = k
    return list(best.values())

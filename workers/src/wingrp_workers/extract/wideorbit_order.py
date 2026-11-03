"""Parser for WideOrbit "ORDER" printouts (political orders).

Seen on Gray stations (WGGB/EGGB/WSHM, Springfield) for 2026. Same traffic
system as `wideorbit.py` (Nexstar "Contract Agreement Between" layout) but a
different print form, so a separate parser fills the same `Contract` model.

Layout:
  page 1 header  `Order / Rev:  4599865`, `Product Desc: ...  WGGB`,
                 `Flight Dates: 10/19/26 - 11/01/26`,
                 `Original Date / Rev: 10/19/26 / 10/19/26`, agency and
                 advertiser blocks (`Name:` rows), `Agency Commission: 15%`
  Bill Plan      per-period `09/28/26 10/25/26  159 $20,395.00 $17,335.75  October 2026 ...`
                 and `Totals  183  $23,980.00  $20,383.00  0.00`
  line items     `N 1 WGGB 10/19/26 10/24/26 <inventory> CM <time> <days> :30 2 $95.00 P- 3 0.00 NM 2 $190.00`
                 (spots/week, rate, priority, rating, type, line spots, amount)
                 then week rows `Week: 10/19/26 10/25/26 -----S- 2 $95.00 0.00`
  last page      `Totals  183  $23,980.00`

The revision date ("Original Date / Rev") is the only revision marker; the
order number stays the same across revisions.
"""

from __future__ import annotations

import re

from .wideorbit import _D, _M, Contract, Line, Period, Week, _cents, _date, _reconcile

FORMAT = "wideorbit_order"
PARSER_VERSION = "2"  # bump when parsing changes, so stored rows can be re-parsed

_LINE_RE = re.compile(
    rf"^\s*([NEDU]?)\s*(\d+)\s+([A-Z]{{3,5}})\s+({_D})\s+({_D})\s+(.*?)\s*"
    rf"(:\d\d|\d+:\d\d)\s+(\d+)\s+{_M}\s+P-?\s*\d+\s+[\d.]+\s+([A-Z]{{2,3}})\s+(\d+)\s+{_M}\s*$"
)
_WEEK_RE = re.compile(rf"^\s*Week:\s+({_D})\s+({_D})\s+([A-Z0-9-]{{7}})\s+(\d+)\s+{_M}\s+[\d.]+\s*$")
_PERIOD_RE = re.compile(rf"^\s*({_D})\s+({_D})\s+(\d+)\s+{_M}\s+{_M}(?:\s|$)")
# Usually its own row; when the bill plan has more periods than months it
# shares the last period's row, so search rather than match.
_BILL_TOTAL_RE = re.compile(rf"(?:^|\s)Totals\s+(\d+)\s+{_M}\s+{_M}\s+[\d.]+\s*$")
_LINE_TOTAL_RE = re.compile(rf"^\s*Totals\s+(\d+)\s+{_M}\s*$")
_DAYS_RE = re.compile(r"[MTWFSu-]{7}$")
# A long inventory name pushes the rest of the line-item row onto the next line.
_LINE_HEAD_RE = re.compile(rf"^\s*[NEDU]?\s*\d+\s+[A-Z]{{3,5}}\s+{_D}\s+{_D}\s")


def is_wideorbit_order(text: str) -> bool:
    head = text[:3000]
    return bool(re.search(r"^\s*ORDER\s*$", head, re.M)) and "Order / Rev:" in head and "Bill Plan" in head


def _field(page1: str, label: str) -> str | None:
    """Value after `Label:` up to the next wide column gap."""
    m = re.search(rf"{re.escape(label)}:[ \t]+(\S.*?)(?:\s{{3,}}|$)", page1, re.M)
    return m.group(1).strip() if m else None


def _parse_header(page1: str, k: Contract) -> None:
    if m := re.search(rf"Print Date\s+({_D})", page1):
        k.print_date = _date(m.group(1))
    if v := _field(page1, "Order / Rev"):
        num, _, rev = v.partition("/")
        k.contract_number = num.strip()
        k.revision = int(rev) if rev.strip().isdigit() else None
    k.alt_order = _field(page1, "Alt Order #")
    if desc := _field(page1, "Product Desc"):
        k.product = desc
    # The call sign is printed at the end of the Product Desc row.
    if m := re.search(r"Product Desc:.*?\s([A-Z]{4})(?:-[A-Z]{2})?\s*$", page1, re.M):
        k.station = m.group(1)
    k.estimate = _field(page1, "Estimate")
    if m := re.search(rf"Flight Dates:\s+({_D})\s*-\s*({_D})", page1):
        k.contract_start, k.contract_end = _date(m.group(1)), _date(m.group(2))
    if m := re.search(rf"Original Date / Rev:\s+({_D})\s*/\s*({_D})", page1):
        k.original_date, k.revision_date = _date(m.group(1)), _date(m.group(2))
    # Two "Name:" rows: the agency block, then the advertiser block.
    blocks = re.split(r"^(Agency|Advertiser)\s", page1, flags=re.M)
    for label, body in zip(blocks[1::2], blocks[2::2]):
        name = _field(body, "Name")
        if label == "Agency" and name:
            k.bill_to = [name]
        elif label == "Advertiser":
            k.advertiser = name
            k.demographic = _field(body, "Demographic")


def _parse_line(m: re.Match[str], page: int) -> Line:
    action, no, ch, start, end, middle, length, _per_week, _rate, spot_type, spots, amount = m.groups()
    days = _DAYS_RE.search(middle)
    middle = middle[: days.start()] if days else middle
    toks = [t for t in re.split(r"\s{2,}", middle.strip()) if t]
    mm, _, ss = length.rpartition(":")
    return Line(
        line_no=int(no),
        action=action or "U",
        channel=ch,
        start=_date(start),
        end=_date(end),
        description=re.sub(r"\s*CM$", "", toks[0]) if toks else "",
        time=toks[-1] if len(toks) > 1 else None,
        days=days.group(0) if days else None,
        length_s=int(mm or 0) * 60 + int(ss),
        spot_type=spot_type,
        spots=int(spots),
        amount_cents=_cents(amount),
        page=page,
    )


def parse_order(text: str) -> Contract:
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    k = Contract(pages=len(pages))
    if not is_wideorbit_order(text):
        k.issues.append("not a WideOrbit order")
        return k
    _parse_header(pages[0], k)

    current: Line | None = None
    line_total: tuple[int, int] | None = None
    for pno, page in enumerate(pages, start=1):
        held: str | None = None
        for raw in page.splitlines():
            if held is not None:
                raw, held = held.rstrip() + "   " + raw.strip(), None
            elif _LINE_HEAD_RE.match(raw) and not _LINE_RE.match(raw):
                held = raw
                continue
            if pno == 1 and (m := _PERIOD_RE.match(raw)):
                start, end, spots, gross, net = m.groups()
                g, n = _cents(gross), _cents(net)
                k.periods.append(Period(_date(start), _date(end), int(spots), g, g - n, n))
            if m := _BILL_TOTAL_RE.search(raw):
                spots, gross, net = m.groups()
                k.total_spots, k.gross_cents, k.net_cents = int(spots), _cents(gross), _cents(net)
                k.commission_cents = k.gross_cents - k.net_cents
            elif m := _LINE_TOTAL_RE.match(raw):
                line_total = (int(m.group(1)), _cents(m.group(2)))
            elif _PERIOD_RE.match(raw) and pno == 1:
                pass
            elif m := _LINE_RE.match(raw):
                current = _parse_line(m, pno)
                k.lines.append(current)
            elif m := _WEEK_RE.match(raw):
                if current is None:
                    k.issues.append(f"page {pno}: week row with no line item")
                    continue
                s, e, days, spots, rate = m.groups()
                w = Week(_date(s), _date(e), days, int(spots), _cents(rate))
                # Revised orders reprint a line's last week row at the top of
                # the next page; keep one copy.
                if any(x.start == w.start for x in current.weeks):
                    if w not in current.weeks:
                        k.issues.append(f"page {pno}: line {current.line_no} lists week {s} twice with different values")
                    continue
                current.weeks.append(w)

    if k.station is None and k.lines:
        k.station = k.lines[0].channel
    _reconcile(k, line_total)
    return k

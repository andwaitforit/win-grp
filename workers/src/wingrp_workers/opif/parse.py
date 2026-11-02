"""Best-effort parsing of OPIF folder paths and file names.

Folder layout: Political Files/{year}/{Federal|State|Local|Non-Candidate Issue Ads}/{office...}/{purchaser}
File names are free text chosen by each station's traffic system. These
parsers only extract what is reliably there. Everything else comes from PDF
extraction later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

ISSUE_ADS = "Non-Candidate Issue Ads"

_CONTRACT_RE = re.compile(r"contract\s*#\s*(\d+)|#\s*(\d{5,})", re.IGNORECASE)
_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})-(\d{1,2})-(\d{2}|\d{4})(?!\d)")


@dataclass(frozen=True)
class FolderInfo:
    pol_year: int | None = None
    pol_category: str | None = None
    pol_office: str | None = None
    purchaser_folder: str | None = None


@dataclass(frozen=True)
class FileNameInfo:
    doc_kind: str = "other"  # nab | order | invoice | other
    is_revision: bool = False
    contract_number: str | None = None
    flight_start: date | None = None
    flight_end: date | None = None


def parse_folder_path(path: str) -> FolderInfo:
    segs = [s for s in path.split("/") if s]
    if len(segs) < 3 or segs[0] != "Political Files" or not segs[1].isdigit():
        return FolderInfo()
    year, category, rest = int(segs[1]), segs[2], segs[3:]
    if category == ISSUE_ADS:
        return FolderInfo(year, category, None, rest[0] if rest else None)
    if category == "Federal":
        # Federal/{office}/{purchaser}[/subfolders...]
        return FolderInfo(
            year,
            category,
            rest[0] if rest else None,
            rest[1] if len(rest) > 1 else None,
        )
    # State/Local nest offices more deeply (e.g. city/office/district); the
    # purchaser is the last segment.
    if len(rest) >= 2:
        return FolderInfo(year, category, "/".join(rest[:-1]), rest[-1])
    return FolderInfo(year, category, rest[0] if rest else None, None)


def _to_date(m: re.Match[str]) -> date | None:
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_file_name(name: str) -> FileNameInfo:
    lower = name.lower()
    # Combined "Order & NAB" documents are orders; check order keywords first.
    if re.search(r"\border\b|contract", lower) and "confirmation" not in lower:
        kind = "order"
    elif re.search(r"(^|[_\s-])nab([_\s-]|$)|pb-?1[89]", lower):
        kind = "nab"
    elif any(k in lower for k in ("invoice", "affidavit", "as run", "as-run", "aired")):
        kind = "invoice"
    elif "order" in lower:
        kind = "order"
    else:
        kind = "other"

    contract = _CONTRACT_RE.search(name)
    dates = [d for d in (_to_date(m) for m in _DATE_RE.finditer(name)) if d]
    start = end = None
    if len(dates) >= 2 and dates[-2] <= dates[-1]:
        start, end = dates[-2], dates[-1]

    return FileNameInfo(
        doc_kind=kind,
        is_revision="revision" in lower or "revised" in lower,
        contract_number=(contract.group(1) or contract.group(2)) if contract else None,
        flight_start=start,
        flight_end=end,
    )

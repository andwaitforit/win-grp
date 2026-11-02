"""GRP estimation (Tier A: spend-based). See docs/SCOPE.md section 4.

    net_cents = gross_cents * (1 - commission)
    GRPs      = net_dollars / CPP(market, demo)      (CPP x 1.2 outside LUR)
    range     = GRPs +/- band

The dashboard repeats this formula in JavaScript so viewers can change CPP
live. Keep the two in sync (see tests/test_estimate.py for reference values).
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from .weeks import overlaps

NON_LUR_CPP_FACTOR = 1.2
DEFAULT_COMMISSION_PCT = 15.0
DEFAULT_BAND_PCT = 20.0

# Lower number wins. Org-licensed data beats a manual race override, which
# beats the placeholder default table.
CPP_SOURCE_PRIORITY = {"org_licensed": 0, "override": 1, "default": 2}


@dataclass(frozen=True)
class CPP:
    market: str
    demo: str
    usd: float
    source: str


def load_cpp(path: str | Path) -> list[CPP]:
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            src = r.get("source", "default").strip() or "default"
            if src not in CPP_SOURCE_PRIORITY:
                raise ValueError(f"cpp.csv: unknown source {src!r} (use {', '.join(CPP_SOURCE_PRIORITY)})")
            usd = float(r["cpp_usd"])
            if usd <= 0:
                raise ValueError(f"cpp.csv: CPP must be positive for {r['market']}")
            rows.append(CPP(market=r["market"].strip(), demo=r["demo"].strip(), usd=usd, source=src))
    return rows


def pick_cpp(rows: list[CPP], market: str, demo: str) -> CPP:
    """Highest-priority CPP for a market and demo."""
    matches = [c for c in rows if c.market == market and c.demo == demo]
    if not matches:
        raise KeyError(f"no CPP for market={market} demo={demo}")
    return min(matches, key=lambda c: CPP_SOURCE_PRIORITY[c.source])


def grps(gross_cents: int, cpp_usd: float, in_lur: bool, commission_pct: float = DEFAULT_COMMISSION_PCT) -> float:
    net_usd = gross_cents / 100 * (1 - commission_pct / 100)
    cpp = cpp_usd * (1 if in_lur else NON_LUR_CPP_FACTOR)
    return net_usd / cpp


def grp_range(value: float, band_pct: float = DEFAULT_BAND_PCT) -> tuple[float, float]:
    return value * (1 - band_pct / 100), value * (1 + band_pct / 100)


def in_any_lur(week: dt.date, windows: dict[str, tuple[dt.date, dt.date]]) -> bool:
    return any(overlaps(week, w) for w in windows.values())

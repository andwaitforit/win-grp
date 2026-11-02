"""Load polls.csv: public and internal polls plus election results."""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from .filings import DataError
from .race import Race

POPULATIONS = ("LV", "RV", "A", "")
KINDS = ("poll", "result")
VISIBILITY = ("public", "internal")


@dataclass(frozen=True)
class Poll:
    field_start: dt.date | None
    field_end: dt.date
    pollster: str
    opponent: str
    our_pct: float
    opp_pct: float
    n: int | None
    population: str
    kind: str
    visibility: str
    source_url: str

    @property
    def margin(self) -> float:
        return round(self.our_pct - self.opp_pct, 1)


def load_polls(path: str | Path, race: Race) -> list[Poll]:
    path = Path(path)
    out = []
    with open(path, newline="") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            where = f"{path.name} row {i}"
            if not any((v or "").strip() for v in r.values()):
                continue
            opp = race.resolve_buyer(r["opponent"])
            if opp is None or opp.side == "us":
                raise DataError(f"{where}: opponent {r['opponent']!r} isn't an opponent in the race config")
            kind = (r.get("kind") or "poll").strip()
            vis = (r.get("visibility") or "public").strip()
            pop = (r.get("population") or "").strip().upper()
            if kind not in KINDS or vis not in VISIBILITY or pop not in POPULATIONS:
                raise DataError(f"{where}: check kind ({'/'.join(KINDS)}), visibility "
                                f"({'/'.join(VISIBILITY)}) and population (LV/RV/A)")
            try:
                out.append(Poll(
                    field_start=dt.date.fromisoformat(r["field_start"]) if (r.get("field_start") or "").strip() else None,
                    field_end=dt.date.fromisoformat(r["field_end"].strip()),
                    pollster=r["pollster"].strip(),
                    opponent=opp.key,
                    our_pct=float(r["our_pct"]),
                    opp_pct=float(r["opp_pct"]),
                    n=int(r["n"]) if (r.get("n") or "").strip() else None,
                    population=pop,
                    kind=kind,
                    visibility=vis,
                    source_url=(r.get("source_url") or "").strip(),
                ))
            except (ValueError, KeyError) as e:
                raise DataError(f"{where}: {e}") from None
    out.sort(key=lambda p: p.field_end)
    return out

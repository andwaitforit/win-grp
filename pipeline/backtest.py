"""Compare our weekly GRP estimates with known GRPs (ground truth).

truth.csv columns: week (any date in the broadcast week), market, buyer, grps.
Estimates come from estimates.csv written by `build`.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

from .filings import DataError
from .race import Race
from .weeks import broadcast_week


def _load(path: Path, race: Race | None, value: str) -> dict[tuple[str, str, str], dict]:
    out: dict[tuple[str, str, str], dict] = defaultdict(lambda: {"v": 0.0, "lo": 0.0, "hi": 0.0})
    with open(path, newline="") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            try:
                wk = broadcast_week(dt.date.fromisoformat(r["week"].strip())).isoformat()
                buyer = r["buyer"].strip()
                if race is not None:
                    b = race.resolve_buyer(buyer)
                    if b is None:
                        raise DataError(f"{path.name} row {i}: unknown buyer {buyer!r}")
                    buyer = b.key
                k = (wk, r["market"].strip(), buyer)
                out[k]["v"] += float(r[value])
                out[k]["lo"] += float(r.get("grps_low") or r[value])
                out[k]["hi"] += float(r.get("grps_high") or r[value])
            except (KeyError, ValueError) as e:
                raise DataError(f"{path.name} row {i}: {e}") from None
    return out


def backtest(estimates_csv: Path, truth_csv: Path, race: Race) -> dict:
    est = _load(estimates_csv, None, "grps")
    truth = _load(truth_csv, race, "grps")
    rows = []
    for k in sorted(set(est) | set(truth)):
        e = est.get(k, {"v": 0.0, "lo": 0.0, "hi": 0.0})
        t = truth.get(k, {"v": 0.0})["v"]
        rows.append({
            "week": k[0], "market": k[1], "buyer": k[2],
            "truth": round(t, 1), "estimate": round(e["v"], 1),
            "error_pct": round((e["v"] - t) / t * 100, 1) if t else None,
            "in_range": e["lo"] <= t <= e["hi"],
        })

    def summarize(group: list[dict]) -> dict:
        scored = [r for r in group if r["error_pct"] is not None]
        tot_t = sum(r["truth"] for r in group)
        tot_e = sum(r["estimate"] for r in group)
        return {
            "weeks": len(group),
            "truth_grps": round(tot_t, 1),
            "estimate_grps": round(tot_e, 1),
            "total_error_pct": round((tot_e - tot_t) / tot_t * 100, 1) if tot_t else None,
            "weekly_mape": round(sum(abs(r["error_pct"]) for r in scored) / len(scored), 1) if scored else None,
            "in_range_pct": round(sum(r["in_range"] for r in scored) / len(scored) * 100) if scored else None,
            "missed_weeks": sum(1 for r in group if r["truth"] and not r["estimate"]),
            "phantom_weeks": sum(1 for r in group if r["estimate"] and not r["truth"]),
        }

    by: dict[str, dict] = {"overall": summarize(rows)}
    for dim in ("buyer", "market"):
        groups: dict[str, list] = defaultdict(list)
        for r in rows:
            groups[r[dim]].append(r)
        for key, g in sorted(groups.items()):
            by[f"{dim}:{key}"] = summarize(g)
    return {"summary": by, "rows": rows}

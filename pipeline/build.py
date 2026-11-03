"""Build the dashboard data file and a weekly estimates CSV from a dataset.

A dataset is a folder with:
  dataset.yaml  as-of date, sample flag, demo, commission, band
  buys.csv      political-file lines (see data/README.md)
  polls.csv     polls and results
  cpp.csv       cost per point by market and demo, with its source
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

import yaml

from . import estimate
from .filings import DataError, group_filings, load_lines, weekly_rows
from .polls import load_polls
from .race import Race, load_race
from .weeks import broadcast_week

MAX_BUYERS = 5  # dashboard has five validated series colors
MAX_ALERTS = 8


def load_dataset_meta(folder: Path) -> dict:
    meta = yaml.safe_load((folder / "dataset.yaml").read_text()) or {}
    meta.setdefault("is_sample", False)
    meta.setdefault("demo", "A35+")
    meta.setdefault("commission_pct", estimate.DEFAULT_COMMISSION_PCT)
    meta.setdefault("band_pct", estimate.DEFAULT_BAND_PCT)
    return meta


def build(race_path: Path, data_dir: Path, as_of: dt.date | None = None) -> tuple[dict, list[dict]]:
    race = load_race(race_path)
    meta = load_dataset_meta(data_dir)
    as_of = as_of or meta.get("as_of") or dt.date.today()
    if isinstance(as_of, str):
        as_of = dt.date.fromisoformat(as_of)

    lines = load_lines(data_dir / "buys.csv", race, require_source=not meta["is_sample"])
    lines = [l for l in lines if l.filed_date <= as_of]
    filings = group_filings(lines)
    rows = weekly_rows(filings, as_of)

    polls = [p for p in load_polls(data_dir / "polls.csv", race) if p.field_end <= as_of]
    cpp_rows = estimate.load_cpp(data_dir / "cpp.csv")

    buyers = _ordered_buyers(race)
    used_markets = [m for m in race.markets.values() if any(s.market == m.key for s in race.stations.values())]
    cpp = {}
    for m in used_markets:
        try:
            cpp[m.key] = estimate.pick_cpp(cpp_rows, m.key, meta["demo"])
        except KeyError:
            raise DataError(f"cpp.csv: no {meta['demo']} CPP for market {m.key}") from None

    data = {
        "meta": {
            "race_id": race.id,
            "title": f"{race.title}: {race.our_side.short_name} vs. "
                     + " / ".join(b.short_name for b in buyers if b.side == "opp" and "general" in b.windows),
            "as_of": as_of.isoformat(),
            "primary_day": race.primary_day.isoformat(),
            "election_day": race.election_day.isoformat(),
            "is_sample": bool(meta["is_sample"]),
            "note": meta.get("note", ""),
            "demo": meta["demo"],
            "our_side": race.our_side.key,
            "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        },
        "buyers": [
            {"key": b.key, "name": b.name, "short_name": b.short_name, "committee": b.committee,
             "side": b.side, "windows": list(b.windows), "slot": i + 1}
            for i, b in enumerate(buyers)
        ],
        "markets": [{"key": m.key, "name": m.name, "short_name": m.short_name, "note": m.note} for m in used_markets],
        "stations": {s.call_sign: {"market": s.market, "network": s.network} for s in race.stations.values()},
        "weeks": {k: [w.isoformat() for w in v] for k, v in race.window_weeks().items()},
        "window_end": {"primary": race.primary_day.isoformat(), "general": race.election_day.isoformat()},
        "lur": [[s.isoformat(), e.isoformat()] for s, e in race.lur_windows.values()],
        "defaults": {
            "cpp": {k: c.usd for k, c in cpp.items()},
            "cpp_source": {k: c.source for k, c in cpp.items()},
            "commission_pct": meta["commission_pct"],
            "band_pct": meta["band_pct"],
        },
        "buys": [
            {"buyer": r.buyer, "station": r.station, "market": r.market, "week": r.week.isoformat(),
             "gross_cents": r.gross_cents, "spots": r.spots, "status": r.status, "confirmed": r.confirmed,
             "filing_id": r.filing_id, "source_url": r.source_url}
            for r in rows
        ],
        "polls": [
            {"date": p.field_end.isoformat(), "opp": p.opponent, "margin": p.margin, "our_pct": p.our_pct,
             "opp_pct": p.opp_pct, "pollster": p.pollster, "n": p.n, "pop": p.population, "kind": p.kind,
             "visibility": p.visibility, "source_url": p.source_url}
            for p in polls
        ],
        "alerts": _alerts(filings),
        # Browser-side source for parsed FCC orders (live datasets only).
        "supabase": meta.get("supabase"),
    }
    estimates = _estimates(race, rows, cpp, meta)
    return data, estimates


def _ordered_buyers(race: Race):
    us = race.our_side
    rest = [b for b in race.buyers.values() if b.key != us.key]
    buyers = [us] + rest
    if len(buyers) > MAX_BUYERS:
        raise DataError(
            f"{len(buyers)} buyers configured; the dashboard can show {MAX_BUYERS}. "
            "Group smaller outside groups into one entry."
        )
    return buyers


def _alerts(filings) -> list[dict]:
    revised = {f.supersedes for f in filings.values() if f.doc_type == "revision"}
    items = sorted(filings.values(), key=lambda f: (f.filed_date, f.id), reverse=True)[:MAX_ALERTS]
    out = []
    for f in items:
        start, end = f.flight
        out.append({
            "date": f.filed_date.isoformat(), "buyer": f.buyer, "doc_type": f.doc_type, "station": f.station,
            "gross_cents": f.gross_cents, "flight_start": start.isoformat(), "flight_end": end.isoformat(),
            "superseded": f.id in revised, "source_url": f.source_url,
        })
    return out


def _estimates(race: Race, rows, cpp, meta) -> list[dict]:
    agg: dict[tuple, dict] = defaultdict(lambda: {"cents": 0, "spots": 0, "statuses": set()})
    for r in rows:
        a = agg[(r.week, r.market, r.buyer)]
        a["cents"] += r.gross_cents
        a["spots"] += r.spots
        a["statuses"].add(r.status)
    out = []
    for (week, market, buyer), a in sorted(agg.items()):
        in_lur = estimate.in_any_lur(week, race.lur_windows)
        c = cpp[market]
        g = estimate.grps(a["cents"], c.usd, in_lur, meta["commission_pct"])
        lo, hi = estimate.grp_range(g, meta["band_pct"])
        out.append({
            "week": week.isoformat(), "market": market, "buyer": buyer, "spots": a["spots"],
            "gross_usd": f"{a['cents'] / 100:.2f}",
            "net_usd": f"{a['cents'] / 100 * (1 - meta['commission_pct'] / 100):.2f}",
            "demo": meta["demo"], "cpp_usd": c.usd, "cpp_source": c.source, "in_lur": in_lur,
            "grps": round(g, 1), "grps_low": round(lo, 1), "grps_high": round(hi, 1),
            "status": "/".join(sorted(a["statuses"])),
        })
    return out


def write_js(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, indent=1, ensure_ascii=False)
    path.write_text(
        "// Generated by `python -m pipeline build`. Do not edit by hand.\n"
        f"window.WIN_GRP_DATA = {body};\n"
    )


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        if not rows:
            f.write("")
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def current_week(as_of: dt.date) -> dt.date:
    return broadcast_week(as_of)

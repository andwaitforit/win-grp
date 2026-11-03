"""Race configuration: candidates, outside groups, markets, stations, dates."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .weeks import broadcast_week


@dataclass(frozen=True)
class Buyer:
    key: str
    name: str
    short_name: str
    committee: str
    side: str  # "us" | "opp" | "ally"
    windows: tuple[str, ...]
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Market:
    key: str
    name: str
    short_name: str
    note: str | None


@dataclass(frozen=True)
class Station:
    call_sign: str
    market: str
    network: str


@dataclass
class Race:
    id: str
    title: str
    primary_day: dt.date
    election_day: dt.date
    lur_windows: dict[str, tuple[dt.date, dt.date]]
    buyers: dict[str, Buyer] = field(default_factory=dict)
    markets: dict[str, Market] = field(default_factory=dict)
    stations: dict[str, Station] = field(default_factory=dict)

    @property
    def our_side(self) -> Buyer:
        return next(b for b in self.buyers.values() if b.side == "us")

    def window_weeks(self) -> dict[str, list[dt.date]]:
        """Broadcast weeks (Mondays) shown for each window.

        Primary: from the week the primary LUR window opens through the week
        of the primary. General: the following week through election week.
        """
        p_start = broadcast_week(self.lur_windows["primary"][0])
        p_end = broadcast_week(self.primary_day)
        g_start = p_end + dt.timedelta(days=7)
        g_end = broadcast_week(self.election_day)
        return {
            "primary": _mondays(p_start, p_end),
            "general": _mondays(g_start, g_end),
        }

    def window_end(self, window: str) -> dt.date:
        return self.primary_day if window == "primary" else self.election_day

    def resolve_buyer(self, text: str) -> Buyer | None:
        """Match a buyer key, name, committee, or alias (case-insensitive)."""
        t = text.strip().lower()
        for b in self.buyers.values():
            names = (b.key, b.name, b.short_name, b.committee) + b.aliases
            if t in (n.lower() for n in names):
                return b
        return None


def _mondays(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def load_race(path: str | Path) -> Race:
    cfg = yaml.safe_load(Path(path).read_text())
    r = cfg["race"]
    lur = {k: (v["start"], v["end"]) for k, v in r["lur_windows"].items()}
    race = Race(
        id=r["id"],
        title=r.get("title") or f'{r["state"]}-{r["district"]}',
        primary_day=r["dates"]["primary"],
        election_day=r["dates"]["general"],
        lur_windows=lur,
    )

    for c in cfg.get("candidates", []):
        committees = c.get("committees") or [{}]
        principal = next((x for x in committees if x.get("role") == "principal"), committees[0])
        race.buyers[c["key"]] = Buyer(
            key=c["key"],
            name=c["name"],
            short_name=c.get("short_name", c["name"]),
            committee=principal.get("name") or c["name"],
            side="us" if c.get("our_side") else "opp",
            windows=tuple(c.get("windows", ["primary", "general"])),
            aliases=tuple(c.get("opif_purchaser_aliases", [])),
        )
    for g in cfg.get("outside_groups") or []:
        race.buyers[g["key"]] = Buyer(
            key=g["key"],
            name=g["name"],
            short_name=g.get("short_name", g["name"]),
            committee=g["name"],
            side=g.get("side", "opp"),
            windows=tuple(g.get("windows", ["primary", "general"])),
            aliases=tuple(g.get("aliases", [])),
        )
    if sum(b.side == "us" for b in race.buyers.values()) != 1:
        raise ValueError("exactly one candidate must have our_side: true")

    for m in cfg.get("markets", []):
        race.markets[m["key"]] = Market(
            key=m["key"],
            name=m["dma"],
            short_name=m.get("short_name", m["dma"]),
            note=m.get("note"),
        )
        for s in m.get("stations") or []:
            # Orders can print a station under another channel name (WSHM-LD as WSHM).
            for name in [s["call_sign"], *(s.get("channels") or [])]:
                race.stations[name] = Station(call_sign=name, market=m["key"], network=s.get("network", ""))
    return race

"""Pipeline tests. Run: python -m unittest discover -s tests"""

import csv
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from pipeline import estimate
from pipeline.backtest import backtest
from pipeline.build import build, write_csv
from pipeline.filings import DataError, group_filings, load_lines, weekly_rows
from pipeline.race import load_race
from pipeline.weeks import allocate_by_week, broadcast_week, overlaps

ROOT = Path(__file__).resolve().parent.parent
RACE = ROOT / "config/pilot/ma-01-2026.yaml"
SAMPLE = ROOT / "data/races/ma-01-2026/sample"
D = dt.date.fromisoformat

COLUMNS = ["filing_id", "doc_type", "filed_date", "buyer", "station", "flight_start", "flight_end",
           "spots", "gross_usd", "supersedes", "source_url", "notes"]


def write_buys(rows: list[dict]) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    w = csv.DictWriter(f, fieldnames=COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow({"supersedes": "", "notes": "", "source_url": "https://publicfiles.fcc.gov/x", **r})
    f.close()
    return Path(f.name)


def row(fid, doc, filed, start, end, gross, spots=10, buyer="milleron", station="WWLP", supersedes=""):
    return {"filing_id": fid, "doc_type": doc, "filed_date": filed, "buyer": buyer, "station": station,
            "flight_start": start, "flight_end": end, "spots": spots, "gross_usd": gross, "supersedes": supersedes}


class WeeksTest(unittest.TestCase):
    def test_broadcast_week_is_monday(self):
        self.assertEqual(broadcast_week(D("2026-11-03")), D("2026-11-02"))
        self.assertEqual(broadcast_week(D("2026-11-08")), D("2026-11-02"))

    def test_allocation_by_days_preserves_totals(self):
        # Wed Oct 7 - Tue Oct 13: 5 days in week of Oct 5, 2 days in week of Oct 12
        out = allocate_by_week(D("2026-10-07"), D("2026-10-13"), 700_00, 7)
        self.assertEqual([(w.isoformat(), c, s) for w, c, s in out],
                         [("2026-10-05", 500_00, 5), ("2026-10-12", 200_00, 2)])

    def test_rounding_remainder_goes_to_last_week(self):
        out = allocate_by_week(D("2026-10-05"), D("2026-10-25"), 100_00, 10)
        self.assertEqual(sum(c for _, c, _ in out), 100_00)
        self.assertEqual(sum(s for _, _, s in out), 10)

    def test_overlaps(self):
        win = (D("2026-09-04"), D("2026-11-03"))
        self.assertTrue(overlaps(D("2026-08-31"), win))   # week includes Sep 4
        self.assertFalse(overlaps(D("2026-08-24"), win))


class EstimateTest(unittest.TestCase):
    # Reference values. The dashboard's JavaScript must produce the same numbers.
    def test_grps_reference_values(self):
        self.assertAlmostEqual(estimate.grps(10_000_00, 85, in_lur=True), 100.0)
        self.assertAlmostEqual(estimate.grps(10_000_00, 85, in_lur=False), 100.0 / 1.2)
        self.assertAlmostEqual(estimate.grps(10_000_00, 85, in_lur=True, commission_pct=0), 10_000 / 85)
        self.assertEqual(estimate.grp_range(100.0, 20), (80.0, 120.0))

    def test_cpp_priority(self):
        rows = [estimate.CPP("spr", "A35+", 85, "default"),
                estimate.CPP("spr", "A35+", 70, "org_licensed"),
                estimate.CPP("spr", "A35+", 90, "override")]
        self.assertEqual(estimate.pick_cpp(rows, "spr", "A35+").source, "org_licensed")
        with self.assertRaises(KeyError):
            estimate.pick_cpp(rows, "alb", "A35+")


class FilingsTest(unittest.TestCase):
    def setUp(self):
        self.race = load_race(RACE)

    def rows_for(self, rows, as_of="2026-10-20"):
        lines = load_lines(write_buys(rows), self.race)
        return weekly_rows(group_filings(lines), D(as_of))

    def test_alias_resolves_buyer(self):
        lines = load_lines(write_buys([row("A", "order", "2026-09-18", "2026-10-05", "2026-10-11", 1000,
                                           buyer="Nadia for Congress")]), self.race)
        self.assertEqual(lines[0].buyer, "milleron")

    def test_revision_replaces_original(self):
        out = self.rows_for([
            row("A", "order", "2026-09-18", "2026-10-05", "2026-10-18", 2000),
            row("B", "revision", "2026-09-22", "2026-10-05", "2026-10-18", 5000, supersedes="A"),
        ])
        self.assertEqual(sum(r.gross_cents for r in out), 5000_00)
        self.assertEqual({r.filing_id for r in out}, {"B"})

    def test_invoice_replaces_only_the_weeks_it_covers(self):
        out = self.rows_for([
            row("A", "order", "2026-09-18", "2026-10-05", "2026-10-18", 2000),
            row("I", "invoice", "2026-10-13", "2026-10-05", "2026-10-11", 1100, supersedes="A"),
        ])
        by_week = {(r.week.isoformat(), r.filing_id): r for r in out}
        self.assertEqual(set(by_week), {("2026-10-05", "I"), ("2026-10-12", "A")})
        self.assertEqual(by_week[("2026-10-05", "I")].gross_cents, 1100_00)
        self.assertTrue(by_week[("2026-10-05", "I")].confirmed)

    def test_invoice_must_point_at_latest_revision(self):
        with self.assertRaises(DataError):
            self.rows_for([
                row("A", "order", "2026-09-18", "2026-10-05", "2026-10-18", 2000),
                row("B", "revision", "2026-09-22", "2026-10-05", "2026-10-18", 5000, supersedes="A"),
                row("I", "invoice", "2026-10-13", "2026-10-05", "2026-10-11", 1100, supersedes="A"),
            ])

    def test_status_relative_to_as_of(self):
        out = self.rows_for([row("A", "order", "2026-09-18", "2026-10-05", "2026-10-25", 3000)], as_of="2026-10-14")
        self.assertEqual([r.status for r in out], ["aired", "airing", "booked"])

    def test_unknown_buyer_and_station_are_errors(self):
        with self.assertRaisesRegex(DataError, "unknown buyer"):
            load_lines(write_buys([row("A", "order", "2026-09-18", "2026-10-05", "2026-10-11", 1, buyer="Mystery PAC")]), self.race)
        with self.assertRaisesRegex(DataError, "unknown station"):
            load_lines(write_buys([row("A", "order", "2026-09-18", "2026-10-05", "2026-10-11", 1, station="WBZ")]), self.race)

    def test_source_url_required_for_live_data(self):
        r = row("A", "order", "2026-09-18", "2026-10-05", "2026-10-11", 1)
        r["source_url"] = ""
        path = write_buys([r])
        with self.assertRaisesRegex(DataError, "source_url"):
            load_lines(path, self.race)
        self.assertEqual(len(load_lines(path, self.race, require_source=False)), 1)


class BuildTest(unittest.TestCase):
    def test_sample_build_matches_prototype_totals(self):
        data, est = build(RACE, SAMPLE)
        general = {"neal": 0.0, "milleron": 0.0}
        for e in est:
            if e["week"] >= "2026-09-07":
                general[e["buyer"]] += e["grps"]
        self.assertEqual(round(general["neal"]), 2431)
        self.assertEqual(round(general["milleron"]), 3675)
        self.assertTrue(data["meta"]["is_sample"])
        self.assertEqual(data["buyers"][0]["key"], "neal")

    def test_filings_after_as_of_are_excluded(self):
        data, _ = build(RACE, SAMPLE, as_of=D("2026-09-20"))
        self.assertFalse(any(a["date"] > "2026-09-20" for a in data["alerts"]))
        self.assertFalse(any(b["buyer"] == "neal" and b["week"] >= "2026-09-07" for b in data["buys"]))


class BacktestTest(unittest.TestCase):
    def test_perfect_estimates_score_zero_error(self):
        _, est = build(RACE, SAMPLE)
        tmp = Path(tempfile.mkdtemp())
        write_csv(est, tmp / "est.csv")
        write_csv([{k: e[k] for k in ("week", "market", "buyer", "grps")} for e in est], tmp / "truth.csv")
        s = backtest(tmp / "est.csv", tmp / "truth.csv", load_race(RACE))["summary"]["overall"]
        self.assertEqual(s["total_error_pct"], 0.0)
        self.assertEqual(s["in_range_pct"], 100)

    def test_missed_weeks_are_counted(self):
        _, est = build(RACE, SAMPLE)
        tmp = Path(tempfile.mkdtemp())
        write_csv(est, tmp / "est.csv")
        truth = [{k: e[k] for k in ("week", "market", "buyer", "grps")} for e in est]
        truth.append({"week": "2026-07-13", "market": "spr", "buyer": "whalen", "grps": 50})
        write_csv(truth, tmp / "truth.csv")
        s = backtest(tmp / "est.csv", tmp / "truth.csv", load_race(RACE))["summary"]["overall"]
        self.assertEqual(s["missed_weeks"], 1)


if __name__ == "__main__":
    unittest.main()

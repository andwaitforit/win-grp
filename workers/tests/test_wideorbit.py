import shutil
from datetime import date
from pathlib import Path

import pytest

from wingrp_workers.extract.pdftext import pdf_to_text
from wingrp_workers.extract.wideorbit import is_wideorbit_contract, latest_versions, parse_contract

FIXTURES = Path(__file__).parents[2] / "fixtures/opif/wideorbit/wwlp-neal-2026"

# (fixture, station, contract #, lines, spots, gross cents, net cents) — from
# each PDF's own Totals block; see the fixture README.
EXPECTED = [
    ("wwlp-5345857", "WWLP", "5345857", 9, 337, 6_996_000, 5_946_600),
    ("wwlp-5345857-updated", "WWLP", "5345857", 9, 337, 6_996_000, 5_946_600),
    ("wwlp-5374935", "WWLP", "5374935", 18, 222, 3_490_500, 2_966_925),
    ("wwlp-5390043", "WWLP", "5390043", 30, 286, 8_982_500, 7_635_125),
    ("wwlp-5421874", "WWLP", "5421874", 24, 226, 7_225_500, 6_141_675),
    ("wwlp-5432661", "WWLP", "5432661", 54, 176, 2_231_500, 1_896_775),
    ("ewlp-5432725", "EWLP", "5432725", 2, 20, 70_000, 59_500),
    ("wwlp-5439765", "WWLP", "5439765", 50, 172, 2_044_000, 1_737_400),
    ("ewlp-5439903", "EWLP", "5439903", 2, 20, 70_000, 59_500),
    ("wwlp-5444906", "WWLP", "5444906", 43, 170, 1_974_000, 1_677_900),
    ("ewlp-5444919", "EWLP", "5444919", 2, 20, 70_000, 59_500),
]


def load(name: str):
    return parse_contract((FIXTURES / f"{name}.txt").read_text())


@pytest.mark.parametrize(("name", "station", "number", "n_lines", "spots", "gross", "net"), EXPECTED)
def test_contract_totals_reconcile(name, station, number, n_lines, spots, gross, net):
    k = load(name)
    assert k.issues == []
    assert (k.station, k.contract_number, len(k.lines)) == (station, number, n_lines)
    assert (k.total_spots, k.gross_cents, k.net_cents) == (spots, gross, net)
    assert k.gross_cents - k.commission_cents == k.net_cents
    assert sum(r["gross_cents"] for r in k.weekly()) == gross


def test_header_fields():
    k = load("wwlp-5439765")
    assert k.advertiser == "POL/Richard Neal/US Congress/MA/Dem"
    assert k.product == "Richard E Neal For Congress Committee"
    assert (k.alt_order, k.estimate, k.demographic) == ("25024102", "10416", "Households")
    assert k.bill_to[0] == "Admire Media LLC"
    assert (k.contract_start, k.contract_end) == (date(2026, 10, 19), date(2026, 11, 1))
    assert k.print_date == date(2026, 10, 19)
    assert k.revision is None

    early = load("wwlp-5345857")
    assert (early.alt_order, early.estimate, early.demographic) == (None, None, "Adults 25-54")


def test_line_items_and_weeks():
    k = load("wwlp-5439765")
    first = k.lines[0]
    assert (first.line_no, first.action, first.channel) == (1, "N", "WWLP")
    assert (first.description, first.time, first.length_s, first.spot_type) == ("22 News @ 6am", "6-6:30a", 30, "NM")
    assert (first.spots, first.amount_cents, first.page) == (1, 6_000, 1)
    assert [(w.start, w.weekdays, w.spots, w.rate_cents) for w in first.weeks] == [
        (date(2026, 10, 19), "-----1-", 1, 6_000)
    ]
    # Line 11 starts on page 1; its week row is printed on page 2.
    l11 = next(ln for ln in k.lines if ln.line_no == 11)
    assert l11.page == 1 and len(l11.weeks) == 1

    # Multi-week line with weekday letters, and free text in the Time column.
    early = load("wwlp-5345857")
    assert len(early.lines[0].weeks) == 6
    assert early.lines[3].description == "M Mass Appeal 11-1130a"


def test_broadcast_month_periods():
    k = load("wwlp-5439765")
    assert [(p.start, p.end, p.spots, p.net_cents) for p in k.periods] == [
        (date(2026, 9, 28), date(2026, 10, 25), 145, 1_453_925),
        (date(2026, 10, 26), date(2026, 11, 29), 27, 283_475),
    ]


def test_latest_versions_dedupes_updated_upload():
    ks = [load(name) for name, *_ in EXPECTED]
    latest = latest_versions(ks)
    assert len(latest) == 10
    dup = [k for k in latest if k.contract_number == "5345857"]
    assert len(dup) == 1 and dup[0].print_date == date(2026, 7, 24)
    assert sum(k.total_spots for k in latest) == 1_649
    assert sum(k.gross_cents for k in latest) == 33_154_000
    assert sum(k.net_cents for k in latest) == 28_180_900


def test_mismatch_is_flagged():
    text = (FIXTURES / "ewlp-5432725.txt").read_text().replace("$630.00", "$640.00", 1)
    k = parse_contract(text)
    assert k.issues and not k.reconciled


def test_non_wideorbit_rejected():
    assert not is_wideorbit_contract("POLITICAL BROADCAST AGREEMENT FORM")
    assert parse_contract("hello").issues == ["not a WideOrbit contract"]


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="poppler not installed")
def test_pdf_text_matches_committed_fixture():
    assert pdf_to_text(FIXTURES / "wwlp-5439765.pdf") == (FIXTURES / "wwlp-5439765.txt").read_text()


MILLERON = Path(__file__).parents[2] / "fixtures/opif/wideorbit/wwlp-milleron-2026"


@pytest.mark.parametrize("name", ["wwlp-5370223", "wwlp-5370223-revision"])
def test_impressions_column_reconciles(name):
    k = parse_contract((MILLERON / f"{name}.txt").read_text())
    assert k.issues == []
    assert k.advertiser == "POL/Nadia Milleron/Congress/MA/Independent"
    assert (k.total_spots, k.gross_cents, k.net_cents) == (191, 8_114_000, 6_896_900)
    assert (k.demographic, k.impressions_000) == ("Adults 18+", 1562.4)
    nfl = next(ln for ln in k.lines if ln.line_no == 25)
    assert [(w.spots, w.rate_cents, w.imp_000) for w in nfl.weeks] == [(2, 600_000, 26.0)]
    assert sum(r["impressions_000"] for r in k.weekly()) == pytest.approx(1562.4)


def test_revision_blank_action_is_unchanged():
    orig = parse_contract((MILLERON / "wwlp-5370223.txt").read_text())
    rev = parse_contract((MILLERON / "wwlp-5370223-revision.txt").read_text())
    assert {ln.action for ln in orig.lines} == {"N"}
    assert [ln.line_no for ln in rev.lines if ln.action == "N"] == [22]
    assert {ln.action for ln in rev.lines if ln.line_no != 22} == {"U"}
    assert latest_versions([orig, rev]) == [rev]

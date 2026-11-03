import shutil
from datetime import date
from pathlib import Path

import pytest

from wingrp_workers.extract.orders import detect
from wingrp_workers.extract.pdftext import pdf_to_text
from wingrp_workers.extract.wideorbit import latest_versions
from wingrp_workers.extract.wideorbit_order import is_wideorbit_order, parse_order

FIXTURES = Path(__file__).parents[2] / "fixtures/opif/wideorbit-order/wggb-2026"

# (fixture, station, order #, revision date, lines, spots, gross cents, net cents)
# from each PDF's own Bill Plan Totals row; see the fixture README.
EXPECTED = [
    ("wggb-4599865", "WGGB", "4599865", date(2026, 10, 19), 59, 183, 2_398_000, 2_038_300),
    ("wshm-4599875", "WSHM", "4599875", date(2026, 10, 19), 52, 124, 919_500, 781_575),
    ("wggb-4539813", "WGGB", "4539813", date(2026, 9, 4), 23, 284, 3_504_000, 2_978_400),
    ("wggb-4539813-revised", "WGGB", "4539813", date(2026, 9, 11), 24, 284, 3_504_000, 2_978_400),
    ("wggb-4490517-whalen", "WGGB", "4490517", date(2026, 7, 21), 12, 62, 777_500, 777_500),
    ("eggb-4611507-milleron", "EGGB", "4611507", date(2026, 10, 29), 10, 15, 88_500, 75_225),
]


def load(name: str):
    return parse_order((FIXTURES / f"{name}.txt").read_text())


@pytest.mark.parametrize(("name", "station", "number", "rev", "n_lines", "spots", "gross", "net"), EXPECTED)
def test_order_totals_reconcile(name, station, number, rev, n_lines, spots, gross, net):
    k = load(name)
    assert k.issues == []
    assert (k.station, k.contract_number, k.revision_date, len(k.lines)) == (station, number, rev, n_lines)
    assert (k.total_spots, k.gross_cents, k.net_cents) == (spots, gross, net)
    assert sum(r["gross_cents"] for r in k.weekly()) == gross


def test_header_fields():
    k = load("wggb-4599865")
    assert k.advertiser == "Richard E Neal for Congress Committee"
    assert (k.alt_order, k.estimate, k.demographic) == ("WOC15936662", "10416", "HH")
    assert k.bill_to == ["Admire Media, LLC"]
    assert (k.contract_start, k.contract_end, k.print_date) == (date(2026, 10, 19), date(2026, 11, 1), date(2026, 10, 19))
    assert [(p.start, p.spots, p.net_cents) for p in k.periods] == [
        (date(2026, 9, 28), 159, 1_733_575),
        (date(2026, 10, 26), 24, 304_725),
    ]


def test_line_items_and_weeks():
    k = load("wggb-4599865")
    first = k.lines[0]
    assert (first.line_no, first.action, first.channel, first.description) == (1, "N", "WGGB", "Sa-Su 7a-8a News")
    assert (first.days, first.length_s, first.spot_type, first.spots, first.amount_cents) == ("-----S-", 30, "NM", 2, 19_000)
    assert [(w.start, w.spots, w.rate_cents) for w in first.weeks] == [(date(2026, 10, 19), 2, 9_500)]
    # Days run into the time column ("12:35 XM-1:05 XMM------").
    assert next(ln for ln in k.lines if ln.line_no == 58).days == "M------"


def test_wrapped_line_item_and_reprinted_week():
    k = load("wggb-4539813-revised")
    # Line 4's inventory name pushes the rest of its row onto the next line.
    l4 = next(ln for ln in k.lines if ln.line_no == 4)
    assert (l4.spots, l4.amount_cents, len(l4.weeks)) == (36, 450_000, 4)
    # Line 12's last week is reprinted at the top of the next page.
    l12 = next(ln for ln in k.lines if ln.line_no == 12)
    assert sum(w.spots for w in l12.weeks) == l12.spots == 6


def test_revision_supersedes_original():
    orig, rev = load("wggb-4539813"), load("wggb-4539813-revised")
    assert latest_versions([orig, rev]) == [rev]


def test_detect_picks_parser_and_rejects_invoices():
    assert detect(load_text("wggb-4599865")).format == "wideorbit_order"
    invoice = load_text("wggb-4539813-invoice")
    assert not is_wideorbit_order(invoice) and detect(invoice) is None
    nexstar = (Path(__file__).parents[2] / "fixtures/opif/wideorbit/wwlp-neal-2026/wwlp-5439765.txt").read_text()
    assert detect(nexstar).format == "wideorbit_contract"


def load_text(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text()


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="poppler not installed")
def test_pdf_text_matches_committed_fixture():
    assert pdf_to_text(FIXTURES / "wggb-4599865.pdf") == load_text("wggb-4599865")

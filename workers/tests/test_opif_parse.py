from datetime import date

import pytest

from wingrp_workers.opif.parse import parse_file_name, parse_folder_path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "Political Files/2026/Federal/US House/Richard Neal",
            (2026, "Federal", "US House", "Richard Neal"),
        ),
        (
            "Political Files/2026/Non-Candidate Issue Ads/Texas PAC",
            (2026, "Non-Candidate Issue Ads", None, "Texas PAC"),
        ),
        (
            "Political Files/2026/State/Governor of Massachusetts/Mike Minogue",
            (2026, "State", "Governor of Massachusetts", "Mike Minogue"),
        ),
        (
            "Political Files/2025/State/New York City/Governor/Steven Fulop",
            (2025, "State", "New York City/Governor", "Steven Fulop"),
        ),
        ("Political Files/2026/Federal", (2026, "Federal", None, None)),
        ("Issues and Programs Lists", (None, None, None, None)),
    ],
)
def test_parse_folder_path(path, expected):
    f = parse_folder_path(path)
    assert (f.pol_year, f.pol_category, f.pol_office, f.purchaser_folder) == expected


# Real WWLP / other-station file names from the OPIF change feed.
@pytest.mark.parametrize(
    ("name", "kind", "rev", "contract", "start", "end"),
    [
        (
            "Committee-To-Elect_Richard Neal_Congress_MA_Democrat_2026_Contract#5390043_09-09-26_09-27-26_WWLP",
            "order", False, "5390043", date(2026, 9, 9), date(2026, 9, 27),
        ),
        (
            "Committee-To-Elect_Ed Markey_US Senate_MA_Democrat_2026_Contract# 5344588_07-20-26_08-02-26_WWLP",
            "order", False, "5344588", date(2026, 7, 20), date(2026, 8, 2),
        ),
        (
            "NAB_Committee-To-Elect_Nadia Milleron_Congress_MA_Independent_2026_WWLP",
            "nab", False, None, None, None,
        ),
        (
            "REVISION_NAB_Committee-To-Elect_Mike Minogue_Governor of Massachusetts",
            "nab", True, None, None, None,
        ),
        (
            "Committee-To-Elect_Nadia Milleron_Congress_MA_Independent_2026_08-31-26_11-1-26_WWLP",
            "other", False, None, date(2026, 8, 31), date(2026, 11, 1),
        ),
        (
            "Committee-To-Elect_Richard Neal_Congress_General Election_MA_Democrat_2026_10-12-26_WWLP",
            "other", False, None, None, None,
        ),
        (
            "senate_leadership_fund_order_confirmation_2111928029_507_pdf",
            "order", False, None, None, None,
        ),
        ("Texas PAC FPM NAB 9.10_1_EWLP", "nab", False, None, None, None),
        ("Richard Neal WTEN Order & NAB #5421304 Revised", "order", True, "5421304", None, None),
        ("Neal WTEN Invoice 5345738-1", "invoice", False, None, None, None),
    ],
)  # fmt: skip
def test_parse_file_name(name, kind, rev, contract, start, end):
    p = parse_file_name(name)
    assert (p.doc_kind, p.is_revision, p.contract_number, p.flight_start, p.flight_end) == (
        kind, rev, contract, start, end,
    )  # fmt: skip


def test_reversed_dates_are_not_a_flight():
    assert parse_file_name("Order_10-01-26_09-01-26").flight_start is None

"""Dashboard views over parsed contracts (local Supabase). Skipped without DATABASE_URL."""

import os
from pathlib import Path

import psycopg
import pytest

from wingrp_workers.extract.store import sha256, store_contract
from wingrp_workers.extract.wideorbit import parse_contract

FIXTURES = Path(__file__).parents[2] / "fixtures/opif/wideorbit"
RACE = "test-race"

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


@pytest.fixture
def conn():
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        # Everything here is rolled back; start from no contracts so synced data doesn't count.
        c.execute("delete from public.opif_contract")
        c.execute("delete from public.race_buyer_alias where race_id = %s", (RACE,))
        c.execute("delete from public.race_station where race_id = %s", (RACE,))
        c.execute(
            "insert into public.race_buyer_alias values (%s, 'neal', 'Richard Neal'), (%s, 'milleron', 'Nadia Milleron')",
            (RACE, RACE),
        )
        c.execute("insert into public.race_station values (%s, 'WWLP', 'spr'), (%s, 'EWLP', 'spr')", (RACE, RACE))
        for rel in ("wwlp-neal-2026/wwlp-5439765", "wwlp-neal-2026/ewlp-5439903", "wwlp-milleron-2026/wwlp-5370223"):
            text = (FIXTURES / f"{rel}.txt").read_text()
            store_contract(
                c, parse_contract(text), pdf_sha256=sha256((FIXTURES / f"{rel}.pdf").read_bytes()),
                raw_text=text, source="user_upload",
            )  # fmt: skip
        yield c
        c.rollback()


def _totals(conn, role=None):
    with conn.transaction(force_rollback=True):
        if role:
            conn.execute(f"set local role {role}")
        return conn.execute(
            "select buyer_key, station, sum(spots), sum(gross_cents) from public.dashboard_buy_week"
            " where race_id = %s group by 1, 2 order by 1, 2",
            (RACE,),
        ).fetchall()


def test_buy_weeks_attribute_buyers_and_match_contract_totals(conn):
    assert _totals(conn) == [
        ("milleron", "WWLP", 191, 8_114_000),
        ("neal", "EWLP", 20, 70_000),
        ("neal", "WWLP", 172, 2_044_000),
    ]


def test_unmatched_purchaser_is_left_out(conn):
    conn.execute("delete from public.race_buyer_alias where race_id = %s and buyer_key = 'milleron'", (RACE,))
    assert [r[0] for r in _totals(conn)] == ["neal", "neal"]


def test_anon_reads_dashboard_views_but_not_tables(conn):
    assert _totals(conn, "anon") == _totals(conn)
    with conn.transaction(force_rollback=True):
        conn.execute("set local role anon")
        assert conn.execute("select count(*) from public.dashboard_filing where race_id = %s", (RACE,)).fetchone()[0] == 3
        for table in ("opif_contract", "opif_contract_week", "race_buyer_alias", "opif_file_extract"):
            assert conn.execute(f"select count(*) from public.{table}").fetchone()[0] == 0


def test_anon_can_only_select_dashboard_views(conn):
    for view in ("dashboard_buy_week", "dashboard_filing", "dashboard_freshness"):
        for priv in ("INSERT", "UPDATE", "DELETE"):
            q = "select has_table_privilege('anon', %s, %s)"
            assert conn.execute(q, (f"public.{view}", priv)).fetchone()[0] is False
    with conn.transaction(force_rollback=True):
        conn.execute("set local role anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select * from internal.race_contract")


# --- Fallback to the last reconciled version, attribution, and counts ---

ORDER_FIXTURES = FIXTURES.parent / "wideorbit-order/wggb-2026"


def _freshness(conn):
    cols = "orders_in_review, orders_on_earlier_version, files_unread, lines_off_station"
    return conn.execute(f"select {cols} from public.dashboard_freshness where race_id = %s", (RACE,)).fetchone()


def _file(conn, n, folder, name, kind="other"):
    """A minimal OPIF file row; returns its file_id."""
    file_id = f"00000000-0000-0000-0000-{n:012d}"
    conn.execute(
        "insert into public.opif_file (file_id, entity_id, call_sign, folder_id, folder_path, file_name,"
        " purchaser_folder, doc_kind) values (%s, '25682', 'WGGB-TV', %s, %s, %s, %s, %s)",
        (file_id, file_id, f"Political Files/2026/Federal/US House/{folder}", name, folder, kind),
    )
    return file_id


def test_revision_in_review_falls_back_to_last_reconciled_version(conn):
    from wingrp_workers.extract.wideorbit_order import parse_order

    conn.execute("insert into public.race_station values (%s, 'WGGB', 'spr')", (RACE,))
    orig = (ORDER_FIXTURES / "wggb-4539813.txt").read_text()
    # The revision no longer adds up to its own totals.
    broken = (ORDER_FIXTURES / "wggb-4539813-revised.txt").read_text().replace("$35,040.00", "$35,041.00")
    k_orig, k_rev = parse_order(orig), parse_order(broken)
    assert k_orig.reconciled and not k_rev.reconciled and k_rev.revision_date > k_orig.revision_date
    store_contract(conn, k_orig, pdf_sha256="a" * 64, raw_text=orig, source="user_upload")
    store_contract(conn, k_rev, pdf_sha256="b" * 64, raw_text=broken, source="user_upload")

    assert ("neal", "WGGB", 284, 3_504_000) in _totals(conn)
    assert conn.execute(
        "select bool_and(revision_in_review) from public.dashboard_buy_week where race_id = %s and station = 'WGGB'",
        (RACE,),
    ).fetchone()[0]
    assert conn.execute(
        "select superseded, revision_in_review from public.dashboard_filing where race_id = %s and station = 'WGGB'",
        (RACE,),
    ).fetchall() == [(False, True)]
    in_review, on_earlier, _, _ = _freshness(conn)
    assert (in_review, on_earlier) == (0, 1)


def test_contract_with_no_reconciled_version_is_counted_in_review(conn):
    text = (FIXTURES / "wwlp-neal-2026/ewlp-5432725.txt").read_text().replace("$630.00", "$640.00", 1)
    k = parse_contract(text)
    assert not k.reconciled
    store_contract(conn, k, pdf_sha256="c" * 64, raw_text=text, source="user_upload")
    assert ("neal", "EWLP", 20, 70_000) in _totals(conn)  # the other EWLP contract, untouched
    assert _freshness(conn)[:2] == (1, 0)


def test_unnumbered_contracts_are_counted_one_by_one(conn):
    for i, rel in enumerate(("wwlp-neal-2026/wwlp-5432661", "wwlp-neal-2026/wwlp-5444906")):
        text = (FIXTURES / f"{rel}.txt").read_text()
        k = parse_contract(text)
        k.contract_number = None
        k.issues.append("header: contract_number not found")
        store_contract(conn, k, pdf_sha256=str(i) * 64, raw_text=text, source="user_upload")
    assert _freshness(conn)[0] == 2


def test_file_name_attributes_a_contract_when_folder_and_advertiser_do_not(conn):
    from wingrp_workers.extract.wideorbit_order import parse_order

    conn.execute("insert into public.race_station values (%s, 'EGGB', 'spr')", (RACE,))
    text = (ORDER_FIXTURES / "eggb-4611507-milleron.txt").read_text()
    k = parse_order(text)
    assert k.advertiser == "Nadia for Congress (Nadia Milleron)"  # matches no alias on its own
    file_id = _file(conn, 1, "Milleron Independent", "Nadia Milleron EGGB 10.30-11.3")
    store_contract(conn, k, pdf_sha256="d" * 64, raw_text=text, source="fcc", source_file_id=file_id)
    assert ("milleron", "EGGB", 15, 88_500) in _totals(conn)


def test_gray_advertiser_attributes_an_uploaded_contract(conn):
    from wingrp_workers.extract.wideorbit_order import parse_order

    conn.execute("insert into public.race_station values (%s, 'WGGB', 'spr')", (RACE,))
    text = (ORDER_FIXTURES / "wggb-4599865.txt").read_text()
    conn.execute("insert into public.race_buyer_alias values (%s, 'neal', 'Richard E Neal for Congress')", (RACE,))
    store_contract(conn, parse_order(text), pdf_sha256="e" * 64, raw_text=text, source="user_upload")
    assert ("neal", "WGGB", 183, 2_398_000) in _totals(conn)


def test_freshness_counts_unread_files_and_lines_off_station(conn):
    assert _freshness(conn)[2:] == (0, 0)
    conn.execute("insert into public.race_station values (%s, 'WGGB-TV', 'spr')", (RACE,))
    for n, (name, outcome) in enumerate(
        [("RICHARD NEAL US CONGRESS 8.22 TO 8.30 WGGB", "unsupported"), ("WGGB Sept Invoice 9.8-9.30", "invoice")]
    ):
        conn.execute(
            "insert into public.opif_file_extract (file_id, outcome, parser, parser_version) values (%s, %s, 'orders', 't')",
            (_file(conn, 10 + n, "Richard Neal", name), outcome),
        )
    conn.execute("delete from public.race_station where race_id = %s and call_sign = 'EWLP'", (RACE,))
    _, _, unread, off_station = _freshness(conn)
    assert unread == 1  # the invoice isn't an unread file
    assert off_station > 0  # EWLP lines no longer map to a race station

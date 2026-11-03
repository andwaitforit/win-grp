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

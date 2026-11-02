"""DB integration for parsed contracts (local Supabase). Skipped without DATABASE_URL."""

import os
from pathlib import Path

import psycopg
import pytest

from wingrp_workers.extract.store import sha256, store_contract
from wingrp_workers.extract.wideorbit import parse_contract

FIXTURES = Path(__file__).parents[2] / "fixtures/opif/wideorbit/wwlp-neal-2026"
NAMES = [
    "wwlp-5345857", "wwlp-5345857-updated", "wwlp-5374935", "wwlp-5390043", "wwlp-5421874",
    "wwlp-5432661", "ewlp-5432725", "wwlp-5439765", "ewlp-5439903", "wwlp-5444906", "ewlp-5444919",
]  # fmt: skip

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


@pytest.fixture
def conn():
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        yield c
        c.rollback()


def _store(conn, name):
    text = (FIXTURES / f"{name}.txt").read_text()
    return store_contract(
        conn, parse_contract(text),
        pdf_sha256=sha256((FIXTURES / f"{name}.pdf").read_bytes()),
        raw_text=text, source="user_upload",
    )  # fmt: skip


def _ids(conn):
    return [_store(conn, n) for n in NAMES]


def test_store_is_idempotent(conn):
    ids = _ids(conn)
    assert _ids(conn) == ids
    lines, weeks = conn.execute(
        "select (select count(*) from public.opif_contract_line where contract_id = any(%s)),"
        "       (select count(*) from public.opif_contract_week where contract_id = any(%s))",
        (ids, ids),
    ).fetchone()
    assert (lines, weeks) == (243, 420)


def test_current_view_dedupes_and_matches_totals(conn):
    ids = _ids(conn)
    n, spots, gross, net, review = conn.execute(
        "select count(*), sum(total_spots), sum(gross_cents), sum(net_cents), bool_or(needs_review)"
        " from public.opif_contract_current where id = any(%s)",
        (ids,),
    ).fetchone()
    assert (n, spots, gross, net, review) == (10, 1_649, 33_154_000, 28_180_900, False)
    kept = conn.execute(
        "select print_date::text from public.opif_contract_current"
        " where id = any(%s) and contract_number = '5345857'",
        (ids,),
    ).fetchall()
    assert kept == [("2026-07-24",)]


def test_weekly_view_sums_to_contract_totals(conn):
    ids = _ids(conn)
    gross, net, spots = conn.execute(
        "select sum(gross_cents), sum(net_cents), sum(spots)"
        " from public.opif_contract_week_current where contract_id = any(%s)",
        (ids,),
    ).fetchone()
    assert (gross, spots) == (33_154_000, 1_649)
    assert abs(net - 28_180_900) <= 210  # per-week rounding, at most half a cent per row


def test_clients_cannot_write_contracts(conn):
    for role in ("anon", "authenticated"):
        with conn.transaction(force_rollback=True):
            conn.execute(f"set local role {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "insert into public.opif_contract (source_pdf_sha256, source, parser, parser_version, raw_text)"
                    " values ('x', 'fcc', 'p', '1', '')"
                )


def test_anon_cannot_read_contracts(conn):
    _store(conn, "ewlp-5432725")
    with conn.transaction(force_rollback=True):
        conn.execute("set local role anon")
        assert conn.execute("select count(*) from public.opif_contract").fetchone()[0] == 0

import json
import os
from collections import defaultdict
from pathlib import Path

import psycopg
import pytest

from wingrp_workers.opif.store import derive_file_state, insert_events, refresh_files

FIXTURE = Path(__file__).parents[2] / "fixtures/opif/wwlp_history_us_house_2026.json"


def _rows():
    return json.loads(FIXTURE.read_text())["history"]


def _events_by_file():
    by_file = defaultdict(list)
    for r in _rows():
        by_file[r["file_id"]].append(
            {"history_status": r["history_status"], "event_ts": r["last_update_ts"], "raw": r}
        )
    return by_file


def test_deleted_and_purged_files_end_deleted():
    states = {fid: derive_file_state(ev) for fid, ev in _events_by_file().items()}
    for fid, ev in _events_by_file().items():
        only_new = all(e["history_status"] == "new" for e in ev)
        assert states[fid]["is_deleted"] is (not only_new), fid


def test_same_timestamp_delete_wins():
    raw = _rows()[0]
    ts = "2026-07-21T18:00:05-04:00"
    state = derive_file_state(
        [
            {"history_status": "new", "event_ts": ts, "raw": raw},
            {"history_status": "delete", "event_ts": ts, "raw": raw},
        ]
    )
    assert state["is_deleted"]


def test_active_wwlp_contracts_have_flights():
    states = [derive_file_state(ev) for ev in _events_by_file().values()]
    active_orders = [s for s in states if not s["is_deleted"] and s["doc_kind"] == "order"]
    assert active_orders
    assert all(s["contract_number"] and s["flight_start"] for s in active_orders)
    assert {s["purchaser_folder"] for s in active_orders} == {
        "Richard Neal", "Nadia Milleron", "Jeromie Whalen",
    }  # fmt: skip


# --- Database integration (local Supabase). Skipped without DATABASE_URL. ---

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


@pytest.fixture
def conn():
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        yield c
        c.rollback()


@needs_db
def test_ingest_is_idempotent(conn):
    rows = _rows()
    n1, touched = insert_events(conn, rows)
    files1 = refresh_files(conn, touched)
    n2, _ = insert_events(conn, rows)
    files2 = refresh_files(conn, touched)
    # Rows may already exist from a real sync; the second pass must add nothing.
    assert n2 == 0
    assert files1 == files2 == len({r["file_id"] for r in rows})


@needs_db
def test_shared_tables_reject_client_writes(conn):
    """Shared public tables are worker-written only: anon and authenticated can't write."""
    raw = _rows()[0]
    for role in ("anon", "authenticated"):
        with conn.transaction(force_rollback=True):
            conn.execute(f"set local role {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "insert into public.opif_file_event (file_id, history_status, event_ts, entity_id, raw) "
                    "values (%s, 'new', now(), '1', '{}')",
                    (raw["file_id"],),
                )


@needs_db
def test_anon_cannot_read_shared_tables(conn):
    insert_events(conn, _rows()[:1])
    with conn.transaction(force_rollback=True):
        conn.execute("set local role anon")
        assert conn.execute("select count(*) from public.opif_file_event").fetchone()[0] == 0

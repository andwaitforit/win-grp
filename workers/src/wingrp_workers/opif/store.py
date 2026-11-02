"""Persist OPIF stations and change-feed events, and derive current file state.

Ingestion is idempotent: events are keyed on (file_id, history_status,
event_ts), and file state is recomputed from all stored events for each
touched file, so re-running any window is safe.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from .parse import parse_file_name, parse_folder_path

DELETED_STATUSES = {"delete", "purge"}


def upsert_station(conn: Connection, facility: dict[str, Any]) -> None:
    conn.execute(
        """
        insert into public.station
          (entity_id, call_sign, service_code, dma, network, community_city,
           community_state, licensee, raw, updated_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        on conflict (entity_id) do update set
          call_sign = excluded.call_sign, service_code = excluded.service_code,
          dma = excluded.dma, network = excluded.network,
          community_city = excluded.community_city,
          community_state = excluded.community_state,
          licensee = excluded.licensee, raw = excluded.raw, updated_at = now()
        """,
        (
            facility["id"],
            facility["callSign"],
            facility.get("serviceCode") or "TV",
            facility.get("nielsenDma"),
            facility.get("networkAfil"),
            facility.get("communityCity"),
            facility.get("communityState"),
            facility.get("partyName"),
            Jsonb(facility),
        ),
    )


def insert_events(conn: Connection, rows: Iterable[dict[str, Any]]) -> tuple[int, set[str]]:
    """Insert raw history rows. Returns (new_event_count, touched_file_ids)."""
    inserted = 0
    touched: set[str] = set()
    with conn.cursor() as cur:
        for r in rows:
            touched.add(r["file_id"])
            cur.execute(
                """
                insert into public.opif_file_event
                  (file_id, history_status, event_ts, entity_id, raw)
                values (%s, %s, %s, %s, %s)
                on conflict (file_id, history_status, event_ts) do nothing
                """,
                (
                    r["file_id"],
                    r["history_status"],
                    r["last_update_ts"],
                    str(r["entity_id"]),
                    Jsonb(r),
                ),
            )
            inserted += cur.rowcount
    return inserted, touched


def _event_order(e: dict[str, Any]) -> tuple[str, int]:
    # On identical timestamps a deletion beats a 'new', so a file deleted in
    # the same second it was posted ends up deleted.
    return (e["event_ts"], 1 if e["history_status"] in DELETED_STATUSES else 0)


def derive_file_state(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the opif_file row for one file from all of its events.

    Each event is {"history_status", "event_ts" (ISO str or datetime), "raw"}.
    Metadata comes from the latest event (it reflects renames/moves); deletion
    state comes from the latest event's status.
    """
    events = sorted(events, key=_event_order)
    latest = events[-1]
    raw = latest["raw"]
    folder = parse_folder_path(raw.get("file_folder_path") or "")
    name = parse_file_name(raw.get("file_name") or "")
    created = min(e["event_ts"] for e in events)
    deleted = latest["history_status"] in DELETED_STATUSES
    return {
        "file_id": raw["file_id"],
        "entity_id": str(raw["entity_id"]),
        "call_sign": raw.get("callsign"),
        "service_code": raw.get("source_service_code"),
        "folder_id": raw["folder_id"],
        "file_manager_id": raw.get("file_manager_id"),
        "folder_path": raw.get("file_folder_path") or "",
        "file_name": raw.get("file_name") or "",
        "file_extension": raw.get("file_extension"),
        "file_size": raw.get("file_size"),
        "fcc_file_status": raw.get("file_status"),
        "fcc_created_at": created,
        "fcc_updated_at": latest["event_ts"],
        "is_deleted": deleted,
        "deleted_at": latest["event_ts"] if deleted else None,
        "pol_year": folder.pol_year,
        "pol_category": folder.pol_category,
        "pol_office": folder.pol_office,
        "purchaser_folder": folder.purchaser_folder,
        "doc_kind": name.doc_kind,
        "is_revision": name.is_revision,
        "contract_number": name.contract_number,
        "flight_start": name.flight_start,
        "flight_end": name.flight_end,
    }


_FILE_COLS = [
    "file_id", "entity_id", "call_sign", "service_code", "folder_id", "file_manager_id", "folder_path",
    "file_name", "file_extension", "file_size", "fcc_file_status", "fcc_created_at",
    "fcc_updated_at", "is_deleted", "deleted_at", "pol_year", "pol_category",
    "pol_office", "purchaser_folder", "doc_kind", "is_revision", "contract_number",
    "flight_start", "flight_end",
]  # fmt: skip


def refresh_files(conn: Connection, file_ids: Iterable[str]) -> int:
    ids = list(file_ids)
    if not ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            select file_id::text, history_status, event_ts, raw
            from public.opif_file_event where file_id = any(%s::uuid[])
            """,
            (ids,),
        )
        by_file: dict[str, list[dict[str, Any]]] = {}
        for file_id, status, ts, raw in cur.fetchall():
            by_file.setdefault(file_id, []).append(
                {"history_status": status, "event_ts": ts, "raw": raw}
            )
        cols = ", ".join(_FILE_COLS)
        placeholders = ", ".join(["%s"] * len(_FILE_COLS))
        updates = ", ".join(f"{c} = excluded.{c}" for c in _FILE_COLS if c != "file_id")
        for events in by_file.values():
            row = derive_file_state(events)
            cur.execute(
                f"insert into public.opif_file ({cols}) values ({placeholders}) "
                f"on conflict (file_id) do update set {updates}, updated_at = now()",
                [row[c] for c in _FILE_COLS],
            )
    return len(by_file)


def get_sync_cursor(conn: Connection, entity_id: str) -> date | None:
    row = conn.execute(
        "select synced_through from public.opif_sync_state where entity_id = %s",
        (entity_id,),
    ).fetchone()
    return row[0] if row else None


def set_sync_cursor(conn: Connection, entity_id: str, through: date, count: int) -> None:
    conn.execute(
        """
        insert into public.opif_sync_state (entity_id, synced_through, last_run_at, last_event_count)
        values (%s, %s, now(), %s)
        on conflict (entity_id) do update set
          synced_through = excluded.synced_through, last_run_at = now(),
          last_event_count = excluded.last_event_count
        """,
        (entity_id, through, count),
    )

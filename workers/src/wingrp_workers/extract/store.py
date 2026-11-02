"""Persist parsed contracts.

Keyed on the PDF's content hash: storing the same PDF again (e.g. after a
parser change) replaces the contract's header, lines, and weeks in place, so
extraction is re-runnable. Version dedupe happens in the
`opif_contract_current` view, not here.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json

from psycopg import Connection
from psycopg.types.json import Jsonb

from .wideorbit import FORMAT, PARSER_VERSION, Contract

_HEADER_COLS = [
    "station", "contract_number", "revision", "alt_order", "advertiser", "product",
    "estimate", "demographic", "bill_to", "contract_start", "contract_end", "print_date",
    "original_date", "revision_date", "total_spots", "gross_cents", "commission_cents",
    "net_cents", "impressions_000", "issues",
]  # fmt: skip


_dumps = functools.partial(json.dumps, default=str)  # dates in period subtotals


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_contract(
    conn: Connection,
    k: Contract,
    *,
    pdf_sha256: str,
    raw_text: str,
    source: str,
    source_file_id: str | None = None,
) -> int:
    """Upsert one parsed contract and its lines/weeks. Returns opif_contract.id."""
    periods = [dataclasses.asdict(p) for p in k.periods]
    cols = ["source_pdf_sha256", "source_file_id", "source", "parser", "parser_version",
            "raw_text", "periods", *_HEADER_COLS]  # fmt: skip
    vals = [pdf_sha256, source_file_id, source, FORMAT, PARSER_VERSION, raw_text,
            Jsonb(periods, dumps=_dumps), *(getattr(k, c) for c in _HEADER_COLS)]  # fmt: skip
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "source_pdf_sha256")
    with conn.cursor() as cur:
        cur.execute(
            f"insert into public.opif_contract ({', '.join(cols)}) "
            f"values ({', '.join(['%s'] * len(cols))}) "
            f"on conflict (source_pdf_sha256) do update set {updates}, parsed_at = now() "
            "returning id",
            vals,
        )
        contract_id = cur.fetchone()[0]
        # Weeks cascade from lines.
        cur.execute("delete from public.opif_contract_line where contract_id = %s", (contract_id,))
        cur.executemany(
            """
            insert into public.opif_contract_line
              (contract_id, line_no, action, channel, start_date, end_date, description,
               time_text, days, length_s, spot_type, spots, amount_cents, source_page)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (contract_id, ln.line_no, ln.action, ln.channel, ln.start, ln.end, ln.description,
                 ln.time, ln.days, ln.length_s, ln.spot_type, ln.spots, ln.amount_cents, ln.page)
                for ln in k.lines
            ],  # fmt: skip
        )
        cur.executemany(
            """
            insert into public.opif_contract_week
              (contract_id, line_no, week_start, week_end, weekdays, spots, rate_cents, imp_000)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (contract_id, ln.line_no, w.start, w.end, w.weekdays, w.spots, w.rate_cents, w.imp_000)
                for ln in k.lines
                for w in ln.weeks
            ],
        )
        if source_file_id:
            cur.execute(
                """
                update public.opif_file
                   set pdf_sha256 = %s, pdf_source = %s, pdf_attached_at = coalesce(pdf_attached_at, now())
                 where file_id = %s
                """,
                (pdf_sha256, source, source_file_id),
            )
    return contract_id


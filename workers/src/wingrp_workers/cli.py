"""win-grp worker CLI.

  wingrp stations --config ../config/pilot/ma-01-2026.yaml
  wingrp sync     --config ../config/pilot/ma-01-2026.yaml [--since 2026-06-01]
  wingrp reparse
  wingrp filings  --config ../config/pilot/ma-01-2026.yaml
  wingrp contract PDF... [--json] [--store [--file-id UUID]]
  wingrp fetch-contracts --config ../config/pilot/ma-01-2026.yaml [--limit N]
  wingrp seed-race --config ../config/pilot/ma-01-2026.yaml
  wingrp reparse-contracts
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import psycopg
import yaml

from .extract.pdftext import pdf_to_text
from .extract.store import sha256, store_contract
from .extract.orders import EXTRACTOR, EXTRACTOR_VERSION, PARSERS, detect
from .extract.wideorbit import latest_versions
from .opif.client import OpifBlocked, OpifClient
from .opif.store import (
    get_sync_cursor,
    insert_events,
    refresh_files,
    set_sync_cursor,
    upsert_station,
)

# Downloaded political-file PDFs, keyed by OPIF file_id (OPIF files are
# immutable), so re-parsing never re-fetches.
PDF_CACHE = Path(__file__).resolve().parents[2] / ".cache" / "opif-pdf"

# Re-read a couple of days behind the cursor: FCC timestamps are local time
# and stations sometimes post late, and event inserts are idempotent anyway.
CURSOR_OVERLAP = timedelta(days=2)


def _connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL (local: postgresql://postgres:postgres@127.0.0.1:55322/postgres)")
    return psycopg.connect(url)


def _race_call_signs(config_path: Path) -> list[str]:
    cfg = yaml.safe_load(config_path.read_text())
    return [s["call_sign"] for m in cfg.get("markets", []) for s in m.get("stations") or []]


def _race_aliases(cfg: dict) -> list[tuple[str, str]]:
    """(buyer_key, alias) for every candidate and outside group in the race config."""
    out = []
    for c in cfg.get("candidates", []):
        out += [(c["key"], a) for a in c.get("opif_purchaser_aliases") or []]
    for g in cfg.get("outside_groups") or []:
        out += [(g["key"], a) for a in g.get("aliases") or []]
    return out


def cmd_seed_race(args: argparse.Namespace) -> None:
    """Load the race's buyer aliases and station markets into the DB (replaces previous rows)."""
    cfg = yaml.safe_load(args.config.read_text())
    race_id = cfg["race"]["id"]
    aliases = _race_aliases(cfg)
    stations = [
        (name, m["key"])
        for m in cfg.get("markets", [])
        for s in m.get("stations") or []
        for name in [s["call_sign"], *(s.get("channels") or [])]
    ]
    with _connect() as conn:
        conn.execute("delete from public.race_buyer_alias where race_id = %s", (race_id,))
        conn.execute("delete from public.race_station where race_id = %s", (race_id,))
        with conn.cursor() as cur:
            cur.executemany(
                "insert into public.race_buyer_alias (race_id, buyer_key, alias) values (%s, %s, %s)"
                " on conflict do nothing",
                [(race_id, k, a) for k, a in aliases],
            )
            cur.executemany(
                "insert into public.race_station (race_id, call_sign, market_key) values (%s, %s, %s)",
                [(race_id, cs, mk) for cs, mk in stations],
            )
        conn.commit()
    print(f"{race_id}: {len(aliases)} purchaser aliases, {len(stations)} stations")


def _record_attempt(conn, file_id: str, sha: str | None, outcome: str) -> None:
    conn.execute(
        """
        insert into public.opif_file_extract (file_id, pdf_sha256, outcome, parser, parser_version)
        values (%s, %s, %s, %s, %s)
        on conflict (file_id) do update
          set pdf_sha256 = excluded.pdf_sha256, outcome = excluded.outcome, parser = excluded.parser,
              parser_version = excluded.parser_version, attempted_at = now()
        """,
        (file_id, sha, outcome, EXTRACTOR, EXTRACTOR_VERSION),
    )


def cmd_stations(args: argparse.Namespace) -> None:
    call_signs = _race_call_signs(args.config)
    with OpifClient() as fcc, _connect() as conn:
        for cs in call_signs:
            results = fcc.search_facility(cs)
            facilities = [
                f
                for key in ("tvFacilityList", "fmFacilityList", "amFacilityList")
                for f in (results.get(key) or [])
                if f.get("callSign", "").upper() == cs.upper()
            ]
            if not facilities:
                print(f"  {cs:<9} NOT FOUND in OPIF facility search")
                continue
            f = facilities[0]
            upsert_station(conn, f)
            print(f"  {cs:<9} entity {f['id']:<7} {f.get('nielsenDma')} ({f.get('networkAfil')})")
        conn.commit()


def cmd_sync(args: argparse.Namespace) -> None:
    call_signs = _race_call_signs(args.config)
    today = date.today()
    with OpifClient() as fcc, _connect() as conn:
        stations = conn.execute(
            "select entity_id, call_sign from public.station where call_sign = any(%s)",
            (call_signs,),
        ).fetchall()
        missing = set(call_signs) - {cs for _, cs in stations}
        if missing:
            print(f"Skipping stations not loaded (run `stations` first or verify): {sorted(missing)}")
        for entity_id, cs in stations:
            cursor = get_sync_cursor(conn, entity_id)
            if args.since:
                start = args.since
            elif cursor:
                start = cursor - CURSOR_OVERLAP
            else:
                sys.exit(f"{cs}: no sync cursor yet; pass --since for the initial backfill")
            rows = list(fcc.file_history(start, today, entity_id=entity_id))
            new_events, touched = insert_events(conn, rows)
            files = refresh_files(conn, touched)
            set_sync_cursor(conn, entity_id, today, len(rows))
            conn.commit()
            print(f"  {cs:<9} {start}..{today}: {len(rows):>4} events ({new_events} new), {files} files updated")


def cmd_reparse(args: argparse.Namespace) -> None:
    """Re-derive opif_file from stored events (e.g. after a parser change). No FCC calls."""
    with _connect() as conn:
        ids = [r[0] for r in conn.execute("select distinct file_id::text from public.opif_file_event")]
        n = refresh_files(conn, ids)
        conn.commit()
    print(f"Re-derived {n} files")


def cmd_filings(args: argparse.Namespace) -> None:
    cfg = yaml.safe_load(args.config.read_text())
    race = cfg["race"]
    office = {"US House": "US House"}.get(race["office"], race["office"])
    call_signs = _race_call_signs(args.config)
    with _connect() as conn:
        rows = conn.execute(
            """
            select f.pol_category, coalesce(f.purchaser_folder, '?') as purchaser,
                   f.call_sign, f.doc_kind, f.contract_number, f.flight_start, f.flight_end,
                   f.fcc_created_at::date, f.is_revision
            from public.opif_file f
            where f.call_sign = any(%s)
              and f.pol_year = %s and not f.is_deleted
              and ((f.pol_category = 'Federal' and f.pol_office = %s)
                   or f.pol_category = 'Non-Candidate Issue Ads')
            order by f.pol_category, purchaser, f.fcc_created_at
            """,
            (call_signs, race["cycle"], office),
        ).fetchall()
    current = None
    for cat, purchaser, cs, kind, contract, fs, fe, created, rev in rows:
        if (cat, purchaser) != current:
            current = (cat, purchaser)
            print(f"\n{cat} / {purchaser}")
        flight = f"{fs}..{fe}" if fs else ""
        print(
            f"  {created}  {cs:<8} {kind:<7}{' (rev)' if rev else '      '} "
            f"{('#' + contract) if contract else '':<10} {flight}"
        )
    print(f"\n{len(rows)} active filings")


def cmd_contract(args: argparse.Namespace) -> None:
    """Parse order PDFs (any supported format); with --store, save them as user uploads."""
    if args.file_id and len(args.pdfs) != 1:
        sys.exit("--file-id links exactly one PDF to its OPIF file")
    conn = _connect() if args.store else None
    parsed = []
    for path in args.pdfs:
        text = pdf_to_text(path)
        parser = detect(text)
        if parser is None:
            print(f"  {path.name}: no parser for this format, skipped", file=sys.stderr)
            continue
        k = parser.parse(text)
        parsed.append(k)
        if conn:
            store_contract(
                conn, k, pdf_sha256=sha256(path.read_bytes()), raw_text=text,
                source="user_upload", source_file_id=args.file_id,
                parser=parser.format, parser_version=parser.version,
            )  # fmt: skip
            conn.commit()
        if args.json:
            print(json.dumps({"source": str(path), **dataclasses.asdict(k)}, default=str))
            continue
        flag = "ok" if k.reconciled else f"REVIEW: {'; '.join(k.issues)}"
        print(
            f"  {k.station:<5} #{k.contract_number} {k.contract_start}..{k.contract_end} "
            f"{k.total_spots:>4} spots  gross ${k.gross_cents / 100:>10,.2f}  "
            f"net ${k.net_cents / 100:>10,.2f}  {flag}"
        )
    if not args.json and len(parsed) > 1:
        latest = latest_versions(parsed)
        print(
            f"\n{len(latest)} contracts after dropping superseded versions: "
            f"{sum(k.total_spots or 0 for k in latest)} spots, "
            f"gross ${sum(k.gross_cents or 0 for k in latest) / 100:,.2f}, "
            f"net ${sum(k.net_cents or 0 for k in latest) / 100:,.2f}"
        )
    if conn:
        conn.close()


def cmd_reparse_contracts(args: argparse.Namespace) -> None:
    """Re-parse stored contracts whose parser version is out of date, from their saved text."""
    current = {p.format: p for p in PARSERS}
    with _connect() as conn:
        rows = conn.execute(
            "select source_pdf_sha256, source_file_id::text, source, parser, parser_version, raw_text"
            " from public.opif_contract"
        ).fetchall()
        n = review = 0
        for sha, file_id, source, parser, version, text in rows:
            p = detect(text)
            if p is None or (p.format == parser and p.version == version and not args.all):
                continue
            k = p.parse(text)
            store_contract(
                conn, k, pdf_sha256=sha, raw_text=text, source=source, source_file_id=file_id,
                parser=p.format, parser_version=p.version,
            )  # fmt: skip
            n += 1
            review += not k.reconciled
        conn.commit()
    print(f"Re-parsed {n} contracts ({review} need review); parsers: {', '.join(f'{f} v{p.version}' for f, p in current.items())}")


def cmd_fetch_contracts(args: argparse.Namespace) -> None:
    """Download, parse, and store race order PDFs that have no parsed contract yet.

    Only files no current parser has tried are fetched (see opif_file_extract).
    """
    cfg = yaml.safe_load(args.config.read_text())
    race = cfg["race"]
    call_signs = _race_call_signs(args.config)
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        files = conn.execute(
            """
            select f.file_id::text, f.folder_id::text, f.file_manager_id::text, f.call_sign, f.file_name
            from public.opif_file f
            where f.call_sign = any(%s) and f.pol_year = %s and not f.is_deleted
              -- File names are free text; "other" files are often orders (WGGB names
              -- them "<buyer> <station> <flight>"). The parser decides by content.
              and f.doc_kind in ('order', 'other')
              and ((f.pol_category = 'Federal' and f.pol_office = %s)
                   or f.pol_category = 'Non-Candidate Issue Ads')
              and not exists (select 1 from public.opif_contract c where c.source_file_id = f.file_id)
              and not exists (select 1 from public.opif_file_extract x
                              where x.file_id = f.file_id and x.parser = %s and x.parser_version = %s)
              -- Only this race's purchasers: Albany stations also carry NY House races.
              and exists (select 1 from unnest(%s::text[]) a(alias)
                          where lower(f.purchaser_folder) = lower(a.alias)
                             or f.file_name ilike '%%' || a.alias || '%%')
            order by f.fcc_created_at
            limit %s
            """,
            (call_signs, race["cycle"], race["office"], EXTRACTOR, EXTRACTOR_VERSION,
             [a for _, a in _race_aliases(cfg)], args.limit),
        ).fetchall()
        print(f"{len(files)} race files not yet tried by {EXTRACTOR_VERSION}")
        stored = review = unsupported = 0
        with OpifClient() as fcc:
            for file_id, folder_id, fm_id, cs, name in files:
                cached = PDF_CACHE / f"{file_id}.pdf"
                if not cached.exists():
                    if not fm_id:
                        print(f"  {cs:<8} {name[:70]}: no file_manager_id; re-run `sync`/`reparse`")
                        continue
                    data = fcc.download(folder_id, fm_id)
                    if data[:4] != b"%PDF":
                        print(f"  {cs:<8} {name[:70]}: response is not a PDF, skipped")
                        _record_attempt(conn, file_id, None, "not_pdf")
                        conn.commit()
                        continue
                    cached.write_bytes(data)
                data = cached.read_bytes()
                text = pdf_to_text(cached)
                parser = detect(text)
                if parser is None:
                    unsupported += 1
                    print(f"  {cs:<8} {name[:70]}: no parser for this format")
                    _record_attempt(conn, file_id, sha256(data), "unsupported")
                    conn.commit()
                    continue
                k = parser.parse(text)
                store_contract(
                    conn, k, pdf_sha256=sha256(data), raw_text=text,
                    source="fcc", source_file_id=file_id,
                    parser=parser.format, parser_version=parser.version,
                )  # fmt: skip
                _record_attempt(conn, file_id, sha256(data), "stored")
                conn.commit()
                stored += 1
                review += not k.reconciled
                flag = "ok" if k.reconciled else "REVIEW"
                print(f"  {k.station or '?':<5} #{k.contract_number} {k.total_spots or 0:>4} spots "
                      f"${(k.gross_cents or 0) / 100:>10,.2f} gross  {flag}")  # fmt: skip
        print(f"\nStored {stored} contracts ({review} need review); {unsupported} unsupported formats")


def main() -> None:
    parser = argparse.ArgumentParser(prog="wingrp")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("stations", help="Resolve race stations to OPIF facilities")
    p.add_argument("--config", type=Path, required=True)
    p.set_defaults(fn=cmd_stations)

    p = sub.add_parser("sync", help="Pull OPIF change-feed events for race stations")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--since", type=date.fromisoformat, help="Backfill start date (YYYY-MM-DD)")
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser("reparse", help="Re-derive file state from stored events (no FCC calls)")
    p.set_defaults(fn=cmd_reparse)

    p = sub.add_parser("filings", help="List active political filings for a race")
    p.add_argument("--config", type=Path, required=True)
    p.set_defaults(fn=cmd_filings)

    p = sub.add_parser("contract", help="Parse order PDFs (no DB, no FCC calls)")
    p.add_argument("pdfs", type=Path, nargs="+")
    p.add_argument("--json", action="store_true", help="One JSON object per contract")
    p.add_argument("--store", action="store_true", help="Save to the DB as a user upload")
    p.add_argument("--file-id", help="OPIF file_id this PDF came from (one PDF only)")
    p.set_defaults(fn=cmd_contract)

    p = sub.add_parser("fetch-contracts", help="Download, parse, and store race order PDFs from OPIF")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(fn=cmd_fetch_contracts)

    p = sub.add_parser("reparse-contracts", help="Re-parse stored contracts after a parser change (no FCC calls)")
    p.add_argument("--all", action="store_true", help="Re-parse every contract, not just out-of-date ones")
    p.set_defaults(fn=cmd_reparse_contracts)

    p = sub.add_parser("seed-race", help="Load race buyer aliases and station markets into the DB")
    p.add_argument("--config", type=Path, required=True)
    p.set_defaults(fn=cmd_seed_race)

    args = parser.parse_args()
    try:
        args.fn(args)
    except OpifBlocked as e:
        sys.exit(f"FCC blocked the request ({e}). Not retrying; raise this with a human.")

# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

**win-grp** is a SaaS tool for political advisors and media buyers. It estimates
an opponent's TV **Gross Rating Points (GRPs)** from FCC political-file ad buys
and shows them next to public polling for the same race.

- Product scope, data-source findings, and GRP methodology: `docs/SCOPE.md`.
  Read it before designing features.
- **Status:** scope approved (decisions in `docs/SCOPE.md` §0). Building the
  Pilot Slice: OPIF ingestion worker (`workers/`) and schema
  (`supabase/migrations/`) exist; no web app yet.
- **Stack (confirmed):** Next.js (TypeScript) on Vercel; Supabase (Postgres,
  Auth, RLS, Storage); Python workers for ingestion and PDF extraction; the
  Claude API for structured extraction.
- **Current goal:** the **2026 Pilot Slice** (`docs/SCOPE.md` §6), live on 2026
  general-election races before election day (Nov 3, 2026). Prefer the
  smallest thing that works for pilot users over completeness.
- **Internal pilot race: MA-01 (Richard Neal vs. Nadia Milleron; primary vs.
  Jeromie Whalen).** Brief: `docs/pilot/MA-01.md`. Seed stations, committees,
  and dates: `config/pilot/ma-01-2026.yaml`. Use this race for fixtures, demos,
  and end-to-end tests. Backtest ground truth will come from the user. Don't
  treat the press reference points in the seed file as ground truth.

## Prototype

- `prototype/ma-01-dashboard.html` is a self-contained, interactive MA-01
  dashboard with static sample data, built to get feedback from consultants.
  Neal is "our side". Keep its sample-data banner; it must never be presented
  as real FCC records. Open it directly in a browser; there is no build step.
- Deployed to Vercel as project `win-grp-prototype` (root directory
  `prototype/`, static, no framework). `prototype/vercel.json` serves the
  dashboard at `/`. Access is Vercel login only (deployment protection on all
  deployments). Don't make it public without the user's say-so.

## Domain glossary

- **OPIF**: FCC Online Public Inspection File (publicfiles.fcc.gov). Stations
  post political ad orders and invoices here.
- **Political file**: OPIF folder tree
  `Political Files/{year}/{Federal|State|Local|Non-Candidate Issue Ads}/{office}/{purchaser}/`.
- **NAB PB-18 / PB-19**: standard political advertising agreement forms that
  identify the buyer, candidate vs. issue, and the office.
- **Order / contract**: planned spot schedule (flight, daypart, spots, rate).
  **Invoice**: what actually aired. Prefer aired numbers; never double-count a
  revised order.
- **DMA**: Nielsen Designated Market Area (210 US TV markets).
- **GRP**: sum of ratings points across all spots against a demo.
  **CPP**: cost per rating point. `GRP ≈ net_spend / CPP`.
- **LUR**: Lowest Unit Rate. Candidates get it 45 days before a primary and
  60 days before a general. Issue groups/PACs don't. Model CPP by buyer class.
- **Share of voice**: one buyer's GRPs ÷ total GRPs in the race.

## Data-source rules

- **Never circumvent bot protection, CAPTCHAs, or rate limits** on FCC or any
  other source (no headless-browser fingerprint spoofing, no rotating proxies).
  If a source blocks automated access, stop and raise it. Sanctioned options are
  listed in `docs/SCOPE.md` §8.
- Identify our client (descriptive User-Agent with contact info), throttle
  requests, and cache aggressively. Treat OPIF files as immutable by content
  hash.
- Every derived number (spend, spots, GRP) must be traceable to its source
  filing (and PDF page where applicable). Keep a `source_*` reference on every
  extracted row.
- GRP outputs are **estimates**. Store and display them as a range with a
  confidence level, never as a single exact figure.
- Polling: preserve pollster, field dates, sample size, population (LV/RV/A),
  and the source URL. Show attribution for CC BY sources (VoteHub). Internal
  polls a customer uploads are tenant-private and must never leak across orgs.
- **Licensed CPP data** uploaded by an org (SQAD/Nielsen-derived) belongs to
  that org's license. Keep it org-scoped, never copy it into shared tables,
  and never use it to calibrate the shared default CPP table. Every GRP
  estimate records which CPP source it used.
- API keys (FEC via api.data.gov, Anthropic, Supabase, Meta) come from env
  vars. Never commit them. Add new vars to `.env.example` with a comment.

## Multi-tenancy and plans

- Shared public data (stations, filings, public polls) and org-scoped data
  (races, internal polls, licensed CPP, overrides, estimates) live in separate
  tables or are separated by `org_id`. Every org-scoped table needs a Supabase
  RLS policy **and** a test that a second org can't read or write it.
- Pricing is **org tiers with an active-race cap** (`plan.max_active_races`).
  Enforce the cap on the server (DB/RPC), not only in the UI. Archived races
  don't count toward it.

## Engineering conventions (until the stack is finalized)

- Keep ingestion (fetch → store raw) separate from extraction
  (raw → structured) and from estimation (structured → GRP). Each stage must be
  re-runnable and idempotent.
- LLM extraction uses structured/JSON-schema output with per-field
  confidence. Low-confidence rows go to a review queue instead of being
  silently accepted.
- Build parser tests from real, anonymized-if-needed sample filings under
  `fixtures/`. Record the station, traffic-system format, and expected output.
- Put money in integer cents and dates in ISO-8601. Broadcast weeks run
  Monday–Sunday. Store both gross and net (after agency commission).

## FCC OPIF access (verified 2026-10-05; PDF download re-tested 2026-11-02)

- Works (plain identified client): facility search
  `/api/service/facility/search/{CALLSIGN}` (no `.json` suffix),
  `/api/service/tv/facility/getall`, folder walk `/api/manager/folder/...`, and
  the change feed `/api/manager/file/history.json` (date range, optional
  `entityId`; rows are `new`/`delete`/`purge` events). Ingestion polls the
  change feed.
- **PDF download works as of 2026-11-02:**
  `/api/manager/download/{folder_id}/{file_manager_id}.pdf` 302s to
  `files.fcc.gov/download/...`. Use `file_manager_id`; `file_id` returns 400.
  It returned an Akamai 403 in October, so it can be blocked again. On a 403,
  `OpifClient` raises `OpifBlocked` and the run stops. Don't work around it;
  fall back to user upload.
- FCC API terms require this notice in the app: "This product uses the FCC Data
  API but is not endorsed or certified by the FCC".
- Purchaser folder names are inconsistent across stations (Neal appears as
  "Richard Neal", "Neal for Congress", "Richard Neal Congress Dist 1 Mass", and
  under a district folder "MA-01" on WNYT). Albany stations also carry NY House
  races. Match purchasers through aliases, never by folder name alone.

## Environment notes

- Local Supabase runs on ports **553xx** (API 55321, DB 55322, Studio 55323),
  not the defaults, so it can coexist with other local Supabase projects.
- The cloud dev environment needs these hosts on its network allowlist:
  `publicfiles.fcc.gov`, `api.votehub.com` / `votehub.com`, and
  `api.open.fec.gov`. They were still blocked as of 2026-09-24. If a request
  fails with a proxy 403, develop against saved fixtures and flag it; don't
  work around the proxy.

## Commands

```bash
supabase start                      # local stack; applies supabase/migrations
supabase db reset                   # rebuild local DB from migrations
cd workers && uv run pytest         # tests (DB tests need DATABASE_URL)
cd workers && uv run wingrp stations --config ../config/pilot/ma-01-2026.yaml
cd workers && uv run wingrp sync     --config ../config/pilot/ma-01-2026.yaml [--since YYYY-MM-DD]
cd workers && uv run wingrp reparse  # re-derive opif_file from stored events, no FCC calls
cd workers && uv run wingrp filings  --config ../config/pilot/ma-01-2026.yaml
cd workers && uv run wingrp fetch-contracts --config ../config/pilot/ma-01-2026.yaml  # download + parse + store orders
cd workers && uv run wingrp contract PDF... [--json] [--store [--file-id UUID]]   # parse local PDFs
```

Contract extraction needs poppler (`pdftotext`). Downloaded PDFs are cached in
`workers/.cache/opif-pdf/` (gitignored). Parsed contracts are in
`opif_contract*`. Sum `opif_contract_current` or `opif_contract_week_current`,
never `opif_contract` itself, which keeps every uploaded version.

Workers read `DATABASE_URL` and `WINGRP_USER_AGENT` from the environment (see
`.env.example`).

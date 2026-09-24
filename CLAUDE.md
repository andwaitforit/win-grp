# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

**win-grp** is a SaaS tool for political advisors and media buyers. It estimates
an opponent's TV **Gross Rating Points (GRPs)** from FCC political-file ad buys
and shows them next to public polling for the same race.

- Product scope, data-source findings, and GRP methodology: `docs/SCOPE.md`.
  Read it before designing features.
- **Status:** planning. No application code yet. The stack in
  `docs/SCOPE.md` §7 (Next.js/Vercel web, Supabase Postgres, Python ingestion
  workers) is a proposal. Confirm it before scaffolding.

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
- API keys (FEC via api.data.gov, Anthropic, Supabase, Meta) come from env
  vars. Never commit them. Add new vars to `.env.example` with a comment.

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

## Environment notes

- The cloud dev sandbox's egress proxy currently **blocks publicfiles.fcc.gov**
  (and votehub.com). Live-API work needs those hosts added to the environment's
  network allowlist. Until then, develop against saved fixtures.

## Commands

None yet. Add build, test, lint, and run commands here once the project is
scaffolded.

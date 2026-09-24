# win-grp: Initial Product Scope

**Status:** Draft for review, 2026-09-24
**Working name:** win-grp (opponent GRP intelligence for political campaigns)

---

## 1. Problem

Political advisors and media buyers need to know **how much TV weight their
opponent is putting behind a message, where, and when**. The standard measure is
**Gross Rating Points (GRPs)**: the sum of the ratings of every spot aired, so
roughly "reach × frequency" against a target audience.

Today they get that picture in one of three ways:

1. **Syndicated trackers** (AdImpact, Vivvix/CMAG, Medium Buying). These are
   accurate and expensive, which puts them out of reach for down-ballot races.
2. **Manually reading FCC political files.** Staff open station PDFs one at a
   time and add up the numbers in spreadsheets. It is slow and error-prone.
3. **Word of mouth from station reps.** Anecdotal and late.

The data behind options 1 and 2 is public: broadcasters must post political ad
buys to the FCC's Online Public Inspection File (OPIF). **win-grp automates
turning those filings into estimated GRPs by market and week, and sets them
next to public polling**, so an advisor can see whether an opponent's
spending is moving the numbers.

## 2. Target users

| Persona | Need | Must-have |
|---|---|---|
| General consultant / advisor | "Is the opponent up on TV, where, how heavy?" | Weekly GRP estimate per DMA, alerts on new buys |
| Media buyer | Plan counter-buys, check rates | Station-level spend, spot counts, CPP benchmarks |
| Campaign manager / finance | Budget planning, fundraising emails | Total opponent spend to date, trend chart |
| Party committee / PAC analyst | Watch many races at once | Multi-race dashboard, exports |

**Initial wedge:** US House, statewide, and competitive state-legislative races,
where syndicated trackers cost too much and one advisor covers many races.

## 3. Data sources (findings)

### 3.1 FCC Online Public Inspection File (OPIF). Core source.

- **What it is:** Every full-power TV station (and, since 2018, cable operators,
  DBS, and radio) must upload political-time requests and dispositions "as soon
  as possible" (in practice, within about one business day) under 47 CFR
  §73.1943. Files are organized as
  `Political Files / {year} / {Federal|State|Local|Non-Candidate Issue Ads} / {office} / {purchaser} / files`.
- **Access:** publicfiles.fcc.gov has a documented, no-auth JSON API
  (developer page at `publicfiles.fcc.gov/developer`, OpenAPI spec at
  `/api/manager/json/apis.json`). It supports facility search, folder
  traversal, and file metadata. Station RSS feeds exist but return only the
  10 most recent uploads, so they are fine for alerts and useless for backfill.
- **What a filing contains:** Mostly **PDFs** in inconsistent formats: NAB
  PB-18/PB-19 agreement forms (who is buying, candidate vs. issue, office),
  station **orders/contracts** (spot schedule: flight dates, daypart/program,
  number of spots, unit rate, gross total), and **invoices** (aired spots, often
  with air times). Formats vary by traffic system (WideOrbit, Imagine/OSi,
  Harris, and others) and some filings are scans.
- **Known obstacles:**
  - Open-source work ([ew-sfex/fcc-political-pipeline](https://github.com/ew-sfex/fcc-political-pipeline))
    reports that **folder/metadata JSON works for plain HTTP clients, but the
    PDF download endpoint returns 403 to automated clients** because of
    Akamai bot protection. **We will not engineer around bot protection.** We
    will ask the FCC for sanctioned bulk or API access (developer@fcc.gov),
    fetch at a polite rate, and let users upload PDFs they already have
    (see Risks). This is the biggest open question in the plan.
  - There is no public callsign → entity-ID lookup. We have to build a facility
    directory from the facility search API and cache it.
  - Folder labels are sometimes wrong, so we classify candidate vs. issue
    ads from the folder tree position and the NAB form together.
  - Orders get **revised and cancelled** (makegoods, preemptions). We need
    order-version logic so the same buy is not counted twice.
  - Digital, CTV/streaming, and most OTT are **not** in OPIF.
- **Even the metadata is useful:** before any PDF parsing, the folder
  tree alone tells us *which purchasers are buying on which stations in which
  weeks*, and that is an alertable signal ("Opponent X just opened a file on
  WXYZ").

### 3.2 FEC (OpenFEC API): cross-check and federal totals

- `api.open.fec.gov` (free key from api.data.gov, rate-limited).
- **Schedule B** (committee disbursements) shows payments to media vendors,
  including the purpose text ("media buy", "TV production"). We can use it to
  sanity-check OPIF totals and to catch placement firms.
- **Schedule E** (independent expenditures, 24/48-hour reports) shows super PAC
  and party IE spend with dates and support/oppose targets. This is
  near-real-time outside-group signal.
- Limits: federal races only. Payments go to buying agencies, not stations, and
  arrive at reporting-period granularity. State races need state-by-state
  campaign-finance portals (post-MVP).

### 3.3 Public polling

| Source | Access | License / notes |
|---|---|---|
| **VoteHub Polls API** | Free JSON, no auth. Fields: pollster, field dates, sample size, population (LV/RV/A), answers[choice, pct], url | CC BY 4.0, so we must show attribution. Beta; coverage of down-ballot races unverified |
| FiveThirtyEight poll CSVs | Static CSV on GitHub | Historical only (538 shut down in 2025). Good for backtesting and pollster ratings |
| Wikipedia race polling tables | Scrape | CC BY-SA. Broad down-ballot coverage, but needs cleanup |
| Roper iPoll | Institutional subscription | Research/historical, not MVP |
| **User-entered / internal polls** | Upload form | Often the most valuable to the customer. Must stay private per tenant |

The MVP polling feature is a trend line per race (simple
recency- and sample-weighted average), with an overlay of opponent GRPs by week
and a marker for each individual poll. We are **not** building a forecasting model.

### 3.4 Digital (post-MVP, for completeness)

- **Google Political Ads**: public BigQuery dataset `google_political_ads`, with
  spend and impression *ranges* by advertiser and region.
- **Meta Ad Library API**: political/issue ads with spend and impression
  *bands*, funder, and region distribution. Requires an identity-verified app.
- These are not GRPs, but they fill the "they went dark on TV and moved to
  digital" blind spot.

### 3.5 Reference data needed for GRP math

- **Station → DMA mapping.** Built from FCC facility data plus a maintained
  mapping table. Nielsen owns DMA definitions, and the list of 210 DMA names
  is widely published.
- **TV households / population per DMA.** Nielsen universe estimates are
  licensed; published TVHH counts are available for rough use.
- **Cost-per-point (CPP) benchmarks** by DMA, daypart, demo, and quarter.
  Industry sources (SQAD, Nielsen) are licensed. MVP plan: seed with
  published rule-of-thumb ranges and let users override. Over time, calibrate
  from the rates we observe in parsed orders combined with any ratings the user
  supplies. This is a moat: a proprietary political CPP table built from
  filings.

## 4. How we estimate GRPs

GRPs are not in the filings; ratings come from Nielsen. So we estimate them
and **always present the result as a range with a confidence level**.

**Tier A (spend-based, works with totals only)**

```
net_spend   = gross_spend × (1 − agency_commission)       # usually 15% when gross
GRP_est     = net_spend / CPP(dma, daypart_mix, demo, week, buyer_class)
```

**Tier B (schedule-based, when the order lists spots by daypart/program)**

```
GRP_est = Σ_spots  est_rating(dma, daypart|program, demo, quarter)
```

This is more accurate. It uses spot counts per daypart, and the rating
estimates can be backed out from CPP and unit rates we have seen before.

**Key political adjustments**

- **Lowest Unit Rate (LUR).** Candidates are entitled to LUR in the 45 days
  before a primary and 60 days before a general election. Issue groups and
  PACs pay market rates, often at a premium. So the **same dollars buy
  materially more GRPs for a candidate than for a super PAC**, and the model
  keys CPP on `buyer_class ∈ {candidate, party, pac_issue}` and the LUR window.
- **Preemptible vs. fixed** spots, and late-cycle rate inflation (Q4 of an
  even year).
- **Ordered vs. aired.** Orders are intent; invoices are what actually ran.
  Show both when available and prefer aired.
- **Revisions.** Build a buy-version chain per (station, purchaser, flight) so
  revisions supersede earlier orders instead of adding to them.

**Outputs per race:** weekly GRPs by DMA × buyer, cumulative GRPs, spend, spot
count, share of voice (their GRPs ÷ everyone's GRPs in the race), and the
standard planning shorthand (for example, "~1,000 GRPs/week ≈ each adult sees
the ad ~10×").

## 5. MVP scope (v0.1)

**In scope**

1. **Race setup.** Pick a race (state, office, district) and the candidates and
   committees in it. The system proposes the DMAs and stations that cover the
   district.
2. **OPIF ingestion.** Walk the political-file folders for the mapped stations
   every few hours. Detect new or changed filings. Store metadata and the
   original PDF (fetched in a sanctioned way or uploaded by a user).
3. **Document extraction.** LLM-assisted extraction (Claude) of order and invoice
   PDFs into structured line items: station, purchaser, flight dates,
   daypart/program, spot count, unit rate, gross. Each field gets a confidence
   score, **every number links back to its source PDF page**, and anything
   below the confidence threshold goes to a human review queue.
4. **GRP estimation.** Tier A for all buys, Tier B where schedules parse. Ranges
   and confidence levels. Editable CPP assumptions per race.
5. **Polling.** Import from VoteHub plus manual or CSV entry. Race trend line
   with the GRP overlay.
6. **Dashboard.** Race view (weekly GRP stacked bar by buyer, spend
   table, poll overlay), station drill-down, and source-document viewer.
7. **Alerts.** Email/Slack when a watched purchaser opens a new file or makes a
   buy bigger than X.
8. **Export.** CSV/XLSX and a shareable read-only race report.
9. **Accounts.** Multi-tenant orgs, email/SSO login, per-org private data
   (internal polls and CPP overrides are never shared across tenants).

**Out of scope for MVP**

- Digital/CTV spend, radio, and cable/DBS political files (the data
  model supports them; we turn them on in phase 2)
- State campaign-finance portals
- Forecasting or vote-share modeling
- Creative/ad-content tracking (which spot is running)
- Nielsen/SQAD licensed data integrations (we design for pluggable CPP
  sources instead)

## 6. Phased roadmap

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **0: Feasibility spike** (1–2 wks) | Facility directory, folder walker for ~20 stations, **confirmed sanctioned route to PDFs**, extraction prototype on 50 real orders | ≥90% field accuracy on spend/spot count and a documented legal/ToS stance |
| **1: MVP** (6–8 wks) | Section 5 above, for TV broadcast | 3 pilot advisors using it on live races |
| **2: Coverage** | Cable/DBS/radio political files, FEC Schedule B/E, Google/Meta digital | "Total opponent media picture" view |
| **3: Moat** | Proprietary political CPP table calibrated from filings, backtests vs. known GRP reports, state finance portals | Estimate error within ±20% vs. ground truth on backtest races |

## 7. Proposed architecture (to confirm)

```
                ┌─────────────────────┐
 FCC OPIF API ──▶                     │    ┌──────────────┐
 FEC API ───────▶  Ingestion workers  ├───▶│  Postgres    │◀──┐
 VoteHub API ───▶  (Python, scheduled)│    │  (Supabase)  │   │
 User uploads ──▶                     │    └──────┬───────┘   │
                └─────────┬───────────┘           │           │
                          ▼                       ▼           │
                ┌─────────────────────┐   ┌──────────────┐    │
                │ Extraction service  │   │  Web app     │    │
                │ PDF → text/OCR →    │   │  Next.js on  │────┘
                │ Claude structured   │   │  Vercel      │
                │ output → review Q   │   └──────────────┘
                └─────────────────────┘
       Object storage (original PDFs, immutable, hashed)
```

- **Web:** Next.js (TypeScript) on Vercel. Auth, RLS, and storage in Supabase.
- **Workers:** Python (strong PDF/OCR tooling: `pdfplumber`, `pymupdf`,
  Tesseract fallback) on a scheduled runner.
- **Extraction:** Claude API with JSON-schema tool output, plus a
  deterministic parser for the most common traffic-system formats (cheaper and
  auditable). The LLM handles the long tail.
- **Core tables:** `station`, `dma`, `race`, `candidate`, `committee`,
  `purchaser` (normalized with aliases), `filing` (OPIF file + hash),
  `buy` / `buy_version`, `line_item`, `cpp_assumption`, `grp_estimate`,
  `poll`, `poll_result`, `alert_rule`, `org`, `user`.

## 8. Risks and open questions

1. **PDF access (critical).** Automated downloads are reportedly blocked by
   bot protection. Options, in order of preference: sanctioned access or
   bulk data from the FCC; a polite, low-rate, identified fetch if permitted
   by the terms; user-uploaded PDFs; a data partnership. **We do not build
   evasion.** This gets decided in Phase 0.
2. **Estimate accuracy.** Without licensed ratings, GRPs are modeled. Mitigation:
   ranges, confidence scores, user-editable CPP, and later, backtesting.
3. **Format diversity** across stations and traffic systems. Mitigation: parser
   per format for the top formats, LLM for the rest, human review queue, and
   source links on every number.
4. **Timeliness.** Stations sometimes upload late or in batches. Show a
   "last filing seen" timestamp per station.
5. **Polling licenses.** Keep attribution for CC-BY sources, CC BY-SA
   share-alike implications if we redistribute Wikipedia-derived data, and
   tenant isolation for private polls.
6. **Competition.** AdImpact and others own the high end. Our position is
   price, speed to insight for down-ballot races, and the poll ↔ GRP
   overlay.

**Questions for you:**

- Which cycle and race types do you want to pilot on (2026 general is live
  right now, or build for the 2027–28 cycle)?
- Do you or your pilot users have **Nielsen/SQAD CPP data** we could plug in,
  or known GRP figures for past races we could backtest against?
- Should the stack assume Supabase + Vercel (both already connected to this
  environment), or do you have a preference?
- Pricing model: per race/month, per seat, or per org tier?
- Are internal polls in scope for v0.1 upload, or are public polls enough?

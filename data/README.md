# Race data

Each race has a folder under `data/races/<race-id>/` with one folder per dataset:

- `sample/`: invented numbers for demos and consultant feedback. The dashboard
  shows a "sample data" banner.
- `live/`: real buys, hand-entered from FCC political files. Every row must
  link to its filing.

The race itself (candidates, outside groups, stations, dates) lives in
`config/pilot/<race>.yaml`.

## Update the dashboard

```sh
python -m pipeline build --race config/pilot/ma-01-2026.yaml \
  --data data/races/ma-01-2026/live \
  --estimates data/races/ma-01-2026/live/estimates.csv
```

This writes `prototype/data/ma-01.js`. Commit and push it, and Vercel redeploys
the dashboard. `--as-of YYYY-MM-DD` shows the race as it looked on that date.
Filings dated later are left out.

## buys.csv: one row per line of a political-file document

Open the station's political file at publicfiles.fcc.gov in your browser
(Political Files → 2026 → Federal → US House → the purchaser). Enter one row
for each order line, or one row for the whole order if it only has totals.

| Column | What to enter |
|---|---|
| `filing_id` | Your ID for the document, e.g. `WWLP-2026-0918-milleron`. Rows from the same PDF share it. |
| `doc_type` | `order`, `revision` (a revised order or contract), or `invoice` (what aired) |
| `filed_date` | Date the station uploaded it (YYYY-MM-DD) |
| `buyer` | Buyer key (`neal`, `milleron`), or the purchaser name as written, if it is listed in the race config |
| `station` | Call sign as in the race config, e.g. `WWLP`, `WGGB-TV` |
| `flight_start`, `flight_end` | First and last air dates of the line |
| `spots` | Number of spots |
| `gross_usd` | Gross dollars (before agency commission) |
| `supersedes` | For a revision, the `filing_id` it replaces. For an invoice, the order it confirms. Otherwise leave blank. |
| `source_url` | Link to the PDF in the political file. Required for live data. |
| `notes` | Anything useful: daypart, programs, "makegood" |

How double counting is avoided:

- A **revision** replaces the filing it names, entirely. Point each new revision
  at the latest previous version.
- An **invoice** replaces the order it names, but only for the weeks the invoice
  covers. Weeks not yet invoiced still count from the order.
- Dollars and spots are spread across broadcast weeks (Monday to Sunday) by
  the number of flight days in each week.

A purchaser that isn't in the race config (a super PAC, for example) is
rejected. Add it under `outside_groups` in the config first. The dashboard
shows up to five buyers.

## polls.csv

| Column | What to enter |
|---|---|
| `field_start`, `field_end` | Field dates (`field_end` is required) |
| `pollster` | Pollster name |
| `opponent` | Opponent key, e.g. `milleron` |
| `our_pct`, `opp_pct` | Topline for our candidate and the opponent |
| `n`, `population` | Sample size and `LV`, `RV`, or `A` |
| `kind` | `poll`, or `result` for certified results |
| `visibility` | `public`, or `internal` for the campaign's own polls |
| `source_url` | Link for public polls. Credit CC BY sources such as VoteHub. |

## cpp.csv

Cost per rating point by market and demo. When a market has several rows, the
highest-priority source wins: `org_licensed` (the customer's SQAD or Nielsen
data), then `override`, then `default` (placeholders). Licensed CPP belongs to
the customer, so don't copy it into another race or organization's data.

## Backtest

When you have actual GRPs, put them in a CSV with the columns `week, market,
buyer, grps`. `week` can be any date in the broadcast week. Then run:

```sh
python -m pipeline backtest --race config/pilot/ma-01-2026.yaml \
  --estimates data/races/ma-01-2026/live/estimates.csv \
  --truth path/to/truth.csv --out backtest.csv
```

It prints total and weekly error, and how often the true value fell inside
our ± range, by buyer and by market.

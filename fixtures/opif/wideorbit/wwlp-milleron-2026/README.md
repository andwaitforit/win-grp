# WWLP — Nadia Milleron, 2026 (WideOrbit contract with impressions)

Real political-file orders, unmodified public FCC records.

- **Station:** WWLP (OPIF entity 6868). Folder
  `Political Files/2026/Federal/US House/Nadia Milleron`
  (`3002ba55-5764-2d3a-cdcb-133504640d08`).
- **Traffic system:** WideOrbit (Aspose.PDF). Downloaded 2026-11-02 through
  `fetch-contracts`.

| Fixture | OPIF file name | Uploaded | Spots | Gross | Net | Imp (000) |
|---|---|---|---|---|---|---|
| wwlp-5370223 | Committee-To-Elect_Nadia Milleron_..._Contract#5370223_08-31-26_09-27-26_WWLP | 2026-08-19 | 191 | $81,140.00 | $68,969.00 | 1,562.4 |
| wwlp-5370223-revision | REVISION_Committee-To-Elect_..._Contract#5370223_08-31-26_09-27-26_WWLP | 2026-08-20 | 191 | $81,140.00 | $68,969.00 | 1,562.4 |

How this format differs from the Neal contracts:
- An **`Imp (000)` column**: the station's impressions estimate per spot, in
  thousands (demo Adults 18+), printed on each week row (`4.2`, or whole
  numbers like `26` for NFL games). The line-items Totals row also carries the
  total (`Totals  1,562.4  191  $81,140.00`). Total = Σ spots × per-spot
  impressions.
- On the revision, unchanged lines have a **blank action column**. Only line 22
  is `N`. The parser records blank as `U`.
- The original date runs into the advertiser cell with no column gap
  (`POL/Nadia Milleron/Congress/MA/Independent 08/20/26`).
- The revision has the same contract number and a later print date (08/20 vs
  08/18), so `latest_versions` keeps it.

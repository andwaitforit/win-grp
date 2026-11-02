# WWLP / EWLP — Richard Neal, 2026 (WideOrbit contracts)

Real political-file orders, unmodified public FCC records.

- **Station:** WWLP (NBC 22, Springfield MA, OPIF entity 6868), plus its
  sister channel EWLP, which is filed in the same folder.
- **Folder:** `Political Files/2026/Federal/US House/Richard Neal`
  (`d57d3729-da91-2a98-5004-8bf1ff1ee102`)
- **Traffic system:** WideOrbit, rendered by Aspose.PDF (Nexstar). Has a text
  layer, so no OCR is needed.
- **Downloaded:** 2026-11-02 through
  `/api/manager/download/{folderId}/{file_manager_id}.pdf` (302 to
  `files.fcc.gov/download/...`) with a plain, identified client.
- `*.txt` is `pdftotext -layout` output, committed so parser tests don't need
  poppler. Pages are separated by `\f`.

| Fixture | OPIF file name | Uploaded | Spots | Gross | Net |
|---|---|---|---|---|---|
| wwlp-5345857 | Committee-To-Elect_Richard Neal_..._Contract#5345857_07-27-26_09-01-26_WWLP | 2026-07-21 | 337 | $69,960.00 | $59,466.00 |
| wwlp-5345857-updated | Updated_Committee-To-Elect_..._Contract#5345857_07-27-26_09-01-26_WWLP | 2026-07-27 | 337 | $69,960.00 | $59,466.00 |
| wwlp-5374935 | ..._Contract#5374935_08-22-26_09-01-26_WWLP | 2026-08-21 | 222 | $34,905.00 | $29,669.25 |
| wwlp-5390043 | ..._Contract#5390043_09-09-26_09-27-26_WWLP | 2026-09-05 | 286 | $89,825.00 | $76,351.25 |
| wwlp-5421874 | ..._Contract#5421874_10-01-26_10-18-26_WWLP | 2026-09-30 | 226 | $72,255.00 | $61,416.75 |
| wwlp-5432661 | ..._Contract#5432661_10-12-26_10-25-26_WWLP | 2026-10-12 | 176 | $22,315.00 | $18,967.75 |
| ewlp-5432725 | ..._Contract#5432725_10-12-26_10-25-26_EWLP | 2026-10-12 | 20 | $700.00 | $595.00 |
| wwlp-5439765 | ..._Contract#5439765_10-19-26_11-01-26_WWLP | 2026-10-19 | 172 | $20,440.00 | $17,374.00 |
| ewlp-5439903 | ..._Contract#5439903_10-19-26_11-01-26_EWLP | 2026-10-19 | 20 | $700.00 | $595.00 |
| wwlp-5444906 | ..._Contract#5444906_10-26-26_11-08-26_WWLP | 2026-10-23 | 170 | $19,740.00 | $16,779.00 |
| ewlp-5444919 | ..._Contract#5444919_10-26-26_11-08-26_EWLP | 2026-10-23 | 20 | $700.00 | $595.00 |

Expected after deduping versions (`latest_versions`): 10 contracts, 1,649
spots, $331,540.00 gross, $281,809.00 net. The updated copy of #5345857 replaces
the original: it has a later print date (07/24 vs 07/21), and its lines start
07/28 rather than 07/27.

Format notes:
- Contracts up to #5421874 are billed to an individual in Northampton with
  demo Adults 25-54, and have no Alt Order # or Estimate #. Later ones are
  billed to Admire Media LLC with demo Households.
- `Product` is free text the station enters ("Add", "September 2026",
  "October", or the committee name).
- The weekdays field in week rows holds per-day spot counts (`-----3-`) or day
  letters (`MTWTF--`).
- A line's week rows can continue onto the next page.
- All lines are `N` (new). No `E`/`D` lines have been seen yet.

# WGGB-TV political file, 2026: WideOrbit "ORDER" print form

Real political-file documents, unmodified public FCC records.

- **Station:** WGGB-TV (OPIF entity 25682, Gray). Its folder also holds orders
  for EGGB and WSHM (CBS 3), each printed as a separate order.
- **Traffic system:** WideOrbit (Aspose.PDF), "ORDER" form. This is a different
  print form from the Nexstar "Contract Agreement Between" layout in
  `../../wideorbit/`. Downloaded 2026-11-03 through the OPIF download endpoint.
- **File names** are free text like `Richard E Neal for Congress Committee WGGB 10.20-11.1`,
  so the file-name classifier tags them `other`. Content detection picks the parser.

| Fixture | OPIF file name | Order # | Rev date | Spots | Gross | Net |
|---|---|---|---|---|---|---|
| wggb-4599865 | Richard E Neal for Congress Committee WGGB 10.20-11.1 | 4599865 | 10/19/26 | 183 | $23,980.00 | $20,383.00 |
| wshm-4599875 | Richard E Neal for Congress Committee WSHM 10.20-11.1 | 4599875 | 10/19/26 | 124 | $9,195.00 | $7,815.75 |
| wggb-4539813 | Richard Neal for US Congress WGGB 9.8-9.30 | 4539813 | 09/04/26 | 284 | $35,040.00 | $29,784.00 |
| wggb-4539813-revised | Richard Neal WGGB 9.8-9.30 Revised | 4539813 | 09/11/26 | 284 | $35,040.00 | $29,784.00 |
| wggb-4490517-whalen | Whalen For Congress WGGB 7.18-9.1.26 | 4490517 | 07/21/26 | 62 | $7,775.00 | $7,775.00 (no agency) |
| eggb-4611507-milleron | Nadia for Congress EGGB 10.30-11.3 | 4611507 | 10/29/26 | 15 | $885.00 | $752.25 |
| wggb-4539813-invoice | Richard Neal for US Congress WGGB Sept Invoice 9.8-9.30 | 4539813-1 | — | — | — | — |

Format notes:
- The line item row carries spots/week, rate, priority, rating, type, line spots and amount.
  A long inventory name pushes the rest of the row onto the next line (4539813-revised line 4).
- The days column can run into the time (`12:35 XM-1:05 XMM------`).
- Revised orders reprint a line's last week row at the top of the next page, followed by
  makegood detail. The parser keeps one copy of the week.
- The order # doesn't change on a revision; the revision date in `Original Date / Rev` does.
- The invoice lists each aired spot with air date, time and ad ID. There's no invoice
  parser yet; it's here so detection is tested to reject it.

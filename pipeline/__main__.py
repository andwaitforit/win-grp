"""win-grp pipeline CLI.

  python -m pipeline build    --race CONFIG --data DIR [--as-of YYYY-MM-DD] [--out JS] [--estimates CSV]
  python -m pipeline backtest --race CONFIG --estimates CSV --truth CSV [--out CSV]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from .backtest import backtest
from .build import build, write_csv, write_js
from .filings import DataError
from .race import load_race


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build dashboard data from a dataset folder")
    b.add_argument("--race", required=True, type=Path)
    b.add_argument("--data", required=True, type=Path)
    b.add_argument("--as-of", type=dt.date.fromisoformat, help="default: dataset.yaml as_of, else today")
    b.add_argument("--out", type=Path, default=Path("prototype/data/ma-01.js"))
    b.add_argument("--estimates", type=Path, help="also write weekly GRP estimates CSV here")

    t = sub.add_parser("backtest", help="compare estimates with known GRPs")
    t.add_argument("--race", required=True, type=Path)
    t.add_argument("--estimates", required=True, type=Path)
    t.add_argument("--truth", required=True, type=Path)
    t.add_argument("--out", type=Path, help="write per-week comparison CSV here")

    args = p.parse_args(argv)
    try:
        if args.cmd == "build":
            data, est = build(args.race, args.data, args.as_of)
            write_js(data, args.out)
            if args.estimates:
                write_csv(est, args.estimates)
            m = data["meta"]
            print(f"Built {args.out} as of {m['as_of']}{' (SAMPLE DATA)' if m['is_sample'] else ''}: "
                  f"{len(data['buys'])} buy-weeks, {len(data['polls'])} polls, {len(data['alerts'])} alerts")
            if args.estimates:
                print(f"Wrote {args.estimates}: {len(est)} week x market x buyer estimates")
        else:
            result = backtest(args.estimates, args.truth, load_race(args.race))
            print(f"{'group':<22}{'weeks':>6}{'truth':>10}{'est.':>10}{'total err':>11}{'wk MAPE':>9}{'in range':>10}")
            for k, s in result["summary"].items():
                fmt = lambda v, suf="": "—" if v is None else f"{v}{suf}"
                print(f"{k:<22}{s['weeks']:>6}{s['truth_grps']:>10}{s['estimate_grps']:>10}"
                      f"{fmt(s['total_error_pct'], '%'):>11}{fmt(s['weekly_mape'], '%'):>9}{fmt(s['in_range_pct'], '%'):>10}")
            if args.out:
                write_csv(result["rows"], args.out)
                print(f"Wrote {args.out}")
    except (DataError, FileNotFoundError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

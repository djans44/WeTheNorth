"""Compute the crests of honour, by hand.

The rules live in app/crests.py, because score entry runs them too. This is
the front end: it prints what it would award and writes nothing unless told.

    python scripts/award_crests.py                 # every season, dry run
    python scripts/award_crests.py --season 2026   # one season, dry run
    python scripts/award_crests.py --apply         # write
"""

import argparse
import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

# Run as a file rather than a module, so the repo root is not on the path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.crests import compute, q, write  # noqa: E402

load_dotenv()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, help="one season; default is all")
    ap.add_argument("--week", type=int, default=1,
                    help="recompute the weekly title snapshots from this week"
                         " on; the rest of the season is done either way")
    ap.add_argument("--apply", action="store_true", help="write, rather than print")
    args = ap.parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        years = [r["season_year"] for r in
                 q(conn, "select season_year from seasons order by season_year")]
        seasons = [args.season] if args.season else years
        crests = {r["code"]: r for r in q(conn, "select * from crests")}
        awards, dropped = compute(conn, seasons, args.week)

        names = {r["owner_id"]: r["username"] for r in
                 q(conn, "select owner_id, username from owners")}
        total = 0
        for code in sorted(awards, key=lambda c: crests[c]["sort_order"]):
            rows = awards[code]
            if not rows:
                continue
            total += len(rows)
            c = crests[code]
            print(f"\n{c['name']}  ({code}, {c['category']}, {c['standing']})")
            shown = sorted(rows, key=lambda r: (r[1] or 0, r[2] or 0, names[r[0]]))
            for oid, year, week, detail, _ in shown[:8]:
                when = (f"{year} wk{week}" if week
                        else (str(year) if year else "career"))
                print("   %-8s %-10s %s" % (names[oid], when, detail or ""))
            if len(shown) > 8:
                print("   ... and %d more" % (len(shown) - 8))

        silent = [c for c, r in crests.items()
                  if r["award_mode"] == "auto" and not awards.get(c)]
        print("\n%d awards across %d crests."
              % (total, len({c for c in awards if awards[c]})))
        if dropped:
            print("%d further qualifying events folded into a crest already "
                  "held in the same slot." % dropped)
        if silent:
            print("Awarded to nobody: " + ", ".join(sorted(silent)))

        if not args.apply:
            print("\nDry run. Nothing written. Pass --apply to write.")
            return
        removed, written = write(conn, awards, args.season, args.week)
        print(f"\nRemoved {removed} auto rows, wrote {written}.")


if __name__ == "__main__":
    main()

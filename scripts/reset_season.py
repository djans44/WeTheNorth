"""Tear one league year down to nothing, on the rehearsal branch only.

    python scripts/reset_season.py 2026            # say what it would delete
    python scripts/reset_season.py 2026 --apply    # do it
    python scripts/reset_season.py 2026 --apply --keep-season

Dry by default, like resolve_phase.py. Refuses to run against the database in
.env -- see scripts/_rehearsal.py.

The order below is derived from the foreign keys rather than chosen by eye:
children before parents, all inside one transaction, so a mistake leaves the
year as it was rather than half gone.

Two columns need more than a delete by season_year:

  keeper_contracts.signed_season  a contract signed in this year is this
                                  year's doing and goes. One signed earlier is
                                  still live and is left alone.
  keeper_contracts.voided_season  a contract voided during this year is not
                                  deleted -- the contract predates the year --
                                  so the void is undone instead.
"""
import os
import sys

import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts._rehearsal import rehearsal_url, describe  # noqa: E402

# child -> parent, so deleting top to bottom never orphans a foreign key.
IN_ORDER = [
    ("keeper_selections",   "season_year = %s"),
    ("keeper_submissions",  "season_year = %s"),
    ("keeper_voids",        "season_year = %s"),
    ("keeper_plans",        "season_year = %s"),
    ("keeper_placeholders", "season_year = %s"),
    ("draft_picks",         "season_year = %s"),
    ("rosters",             "season_year = %s"),
    ("transactions",        "season_year = %s"),
    ("matchups",            "season_year = %s"),
    ("owner_crests",        "season_year = %s"),
    ("keeper_windows",      "season_year = %s"),
    ("keeper_contracts",    "signed_season = %s"),
    ("rivalries",           "season_year = %s"),
    ("player_adp",          "season_year = %s"),
    ("draft_order",         "season_year = %s"),
    ("teams",               "season_year = %s"),
]

def main():
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        raise SystemExit(__doc__)
    year = int(sys.argv[1])
    apply_it = "--apply" in sys.argv
    keep_season = "--keep-season" in sys.argv
    url = rehearsal_url()

    print("season      %s" % year)
    print("host        %s" % describe(url))
    print("mode        %s" % ("APPLY -- rows will be deleted" if apply_it else "dry run"))
    print()

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            total = 0
            for table, where in IN_ORDER:
                cur.execute("select count(*) from %s where %s" % (table, where), (year,))
                n = cur.fetchone()[0]
                total += n
                if n:
                    print("  %-22s %6d" % (table, n))
                if n and apply_it:
                    cur.execute("delete from %s where %s" % (table, where), (year,))

            cur.execute("select count(*) from keeper_contracts where voided_season = %s", (year,))
            unvoid = cur.fetchone()[0]
            if unvoid:
                print("  %-22s %6d  (void undone, contract kept)" % ("keeper_contracts", unvoid))
                if apply_it:
                    cur.execute("""update keeper_contracts
                                   set voided_season = null, status = 'active'
                                   where voided_season = %s""", (year,))

            seasons_n = 0
            if not keep_season:
                cur.execute("select count(*) from seasons where season_year = %s", (year,))
                seasons_n = cur.fetchone()[0]
                if seasons_n:
                    print("  %-22s %6d" % ("seasons", seasons_n))
                    if apply_it:
                        cur.execute("delete from seasons where season_year = %s", (year,))

            print()
            print("  %-22s %6d rows" % ("total", total + unvoid + seasons_n))
            if apply_it:
                conn.commit()
                print("\ndeleted.")
            else:
                conn.rollback()
                print("\nnothing written. add --apply to do it.")


if __name__ == "__main__":
    main()

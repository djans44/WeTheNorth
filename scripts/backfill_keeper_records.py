# -*- coding: utf-8 -*-
"""Give a resolved season the contracts and the archive it never got.

    python scripts/backfill_keeper_records.py 2026          # says what it would do
    python scripts/backfill_keeper_records.py 2026 --apply  # does it

Until now nothing in the app wrote keeper_contracts or keeper_selections --
only the two history importers. A season could resolve all three keeper
rounds and leave both empty, which is what 2026 did: six signings existing
only as a term on a submission, and no archive at all. The routes write both
from now on; this is for the season that resolved before they did.

It uses the same two functions the routes use, so a season backfilled here is
indistinguishable afterwards from one done live. Run it twice and the second
run finds nothing to sign and rebuilds the same archive, because the rebuild
is derived rather than accumulated.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()
import psycopg

from app.main import adp_round_for, sign_contract, sync_keeper_selections

season = int(sys.argv[1])
apply = "--apply" in sys.argv
url = os.environ.get("DATABASE_URL_DIRECT") or os.environ["DATABASE_URL"]

with psycopg.connect(url, row_factory=psycopg.rows.dict_row) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            select s.submission_id, s.season_year, s.owner_id, s.player_id,
                   s.cost_round, s.term_years, o.username, p.full_name
            from keeper_submissions s
            join owners o on o.owner_id = s.owner_id
            join players p on p.player_id = s.player_id
            where s.season_year = %s and s.status = 'approved'
              and s.term_years is not null and s.contract_id is null
            order by o.username
        """, (season,))
        signings = cur.fetchall()

    print("%d signing%s with no contract:" % (len(signings),
                                              "" if len(signings) == 1 else "s"))
    for sub in signings:
        adp = adp_round_for(conn, season, sub["player_id"])
        later = (min(sub["cost_round"], min((adp or 13) + 2, 13))
                 if sub["term_years"] == 3 else None)
        print("   %-8s %-21s R%-3s %s yr  adp round %-4s later %s"
              % (sub["username"], sub["full_name"], sub["cost_round"],
                 sub["term_years"], adp if adp is not None else "none",
                 ("R%d" % later) if later else "n/a"))

    if not apply:
        print("\nDry run. Nothing written. Add --apply to write it.")
        raise SystemExit(0)

    with conn.cursor() as cur:
        for sub in signings:
            adp = adp_round_for(conn, season, sub["player_id"])
            cur.execute("""
                update keeper_submissions
                set contract_id = %s, term_years = null
                where submission_id = %s
            """, (sign_contract(cur, sub, adp), sub["submission_id"]))
    conn.commit()
    written = sync_keeper_selections(conn, season)
    conn.commit()
    print("\n%d contracts signed, %d selections written." % (len(signings), written))

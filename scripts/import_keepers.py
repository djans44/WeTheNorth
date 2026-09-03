import csv
import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()

path = pathlib.Path(sys.argv[1])
url = os.environ.get("DATABASE_URL_DIRECT") or os.environ["DATABASE_URL"]
rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
print(f"read {len(rows)} rows")


def num(v):
    v = (v or "").strip()
    return int(v) if v else None


with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("select season_year, team_name, team_id from teams")
        teams = {(y, n.strip().lower()): tid for y, n, tid in cur.fetchall()}
        cur.execute("""
            select t.team_id, t.owner_id from teams t
        """)
        owner_of = dict(cur.fetchall())
        cur.execute("select player_id, lower(full_name) from players")
        players = {}
        for pid, name in cur.fetchall():
            players.setdefault(name, pid)

    problems = []
    for i, r in enumerate(rows, start=2):
        y = int(r["season_year"])
        if (y, r["team_name"].strip().lower()) not in teams:
            problems.append(f"line {i}: no {y} team {r['team_name']!r}")
        if r["player_name"].strip().lower() not in players:
            problems.append(f"line {i}: no player {r['player_name']!r}")
    if problems:
        print("aborted, nothing written:")
        for p in problems:
            print("  " + p)
        raise SystemExit(1)

    season = int(rows[0]["season_year"])
    with conn.cursor() as cur:
        cur.execute("delete from keeper_selections where season_year = %s", (season,))

        for r in rows:
            team_id = teams[(season, r["team_name"].strip().lower())]
            player_id = players[r["player_name"].strip().lower()]
            owner_id = owner_of[team_id]
            signed = num(r["signed_season"])
            contract_id = None

            if signed:
                cur.execute("""
                    insert into keeper_contracts
                        (player_id, owner_id, original_round, signed_season,
                         contract_years, contract_round)
                    values (%s, %s, %s, %s, %s, %s)
                    on conflict (player_id, signed_season) do update set
                        owner_id       = excluded.owner_id,
                        original_round = excluded.original_round,
                        contract_years = excluded.contract_years,
                        contract_round = excluded.contract_round,
                        updated_at     = now()
                    returning contract_id
                """, (player_id, owner_id, num(r["original_round"]), signed,
                      num(r["contract_years"]), num(r["contract_round"])))
                contract_id = cur.fetchone()[0]

            cur.execute("""
                insert into keeper_selections
                    (season_year, team_id, player_id, cost_round,
                     keeper_year, contract_id)
                values (%s, %s, %s, %s, %s, %s)
            """, (season, team_id, player_id, num(r["cost_round"]),
                  num(r["keeper_year"]), contract_id))

        cur.execute("""
            update draft_picks set is_keeper = true
            where season_year = %s
              and (round, pick_in_round) in (
                  select d.round, d.pick_in_round
                  from draft_picks d
                  join keeper_selections k
                    on k.season_year = d.season_year
                   and k.player_id   = d.player_id
                  where d.season_year = %s
              )
        """, (season, season))
    conn.commit()

print(f"imported {len(rows)} keeper selections")

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

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("select season_year, team_name, team_id from teams")
        teams = {(y, n.strip().lower()): tid for y, n, tid in cur.fetchall()}

    missing = [f"line {i}: no {r['season_year']} team {r['team_name']!r}"
               for i, r in enumerate(rows, start=2)
               if (int(r["season_year"]), r["team_name"].strip().lower()) not in teams]
    if missing:
        print("aborted, nothing written:")
        for m in missing:
            print("  " + m)
        raise SystemExit(1)

    with conn.cursor() as cur:
        for r in rows:
            cur.execute("""
                insert into players (full_name, position) values (%s, %s)
                on conflict (lower(full_name), position) do nothing
            """, (r["player_name"].strip(), r["position"].strip()))
        conn.commit()

        cur.execute("select player_id, lower(full_name), position from players")
        players = {(n, p): pid for pid, n, p in cur.fetchall()}

        season = int(rows[0]["season_year"])
        cur.execute("delete from draft_picks where season_year = %s", (season,))
        cur.executemany("""
            insert into draft_picks
                (season_year, round, pick_in_round, team_id, player_id, is_keeper)
            values (%s, %s, %s, %s, %s, %s)
        """, [(
            int(r["season_year"]), int(r["round"]), int(r["pick_in_round"]),
            teams[(int(r["season_year"]), r["team_name"].strip().lower())],
            players[(r["player_name"].strip().lower(), r["position"].strip())],
            (r.get("is_keeper") or "").strip().lower() in ("1", "true", "yes"),
        ) for r in rows])
    conn.commit()

print(f"imported {len(rows)} picks")

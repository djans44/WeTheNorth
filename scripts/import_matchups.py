import csv
import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "data/matchups.csv")
url = os.environ.get("DATABASE_URL_DIRECT") or os.environ["DATABASE_URL"]

rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
print(f"read {len(rows)} rows from {path}")


def num(value):
    value = (value or "").strip()
    return value or None


with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("select season_year, team_name, team_id from teams")
        lookup = {(y, n.strip().lower()): tid for y, n, tid in cur.fetchall()}

    missing = []
    prepared = []

    for i, r in enumerate(rows, start=2):
        year = int(r["season_year"])
        a_name = r["team_a"].strip()
        b_name = r["team_b"].strip()

        a_key = (year, a_name.lower())
        if a_key not in lookup:
            missing.append(f"line {i}: no {year} team named {a_name!r}")
            continue

        a_id = lookup[a_key]
        a_pts, a_proj = num(r.get("points_a")), num(r.get("projected_a"))

        if not b_name:
            prepared.append((
                year, int(r["week"]),
                r.get("game_type", "regular").strip() or "regular",
                a_id, None, a_pts, None, a_proj, None,
            ))
            continue

        b_key = (year, b_name.lower())
        if b_key not in lookup:
            missing.append(f"line {i}: no {year} team named {b_name!r}")
            continue

        b_id = lookup[b_key]
        b_pts, b_proj = num(r.get("points_b")), num(r.get("projected_b"))

        if a_id > b_id:
            a_id, b_id = b_id, a_id
            a_pts, b_pts = b_pts, a_pts
            a_proj, b_proj = b_proj, a_proj

        prepared.append((
            year, int(r["week"]),
            r.get("game_type", "regular").strip() or "regular",
            a_id, b_id, a_pts, b_pts, a_proj, b_proj,
        ))

    if missing:
        print("\naborted, nothing written:")
        for m in missing:
            print("  " + m)
        raise SystemExit(1)

    with conn.cursor() as cur:
        cur.executemany("""
            insert into matchups
                (season_year, week, game_type, team_a_id, team_b_id,
                 team_a_points, team_b_points, team_a_projected, team_b_projected)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (season_year, week, team_a_id, team_b_id)
            do update set
                game_type        = excluded.game_type,
                team_a_points    = excluded.team_a_points,
                team_b_points    = excluded.team_b_points,
                team_a_projected = excluded.team_a_projected,
                team_b_projected = excluded.team_b_projected,
                updated_at       = now()
        """, prepared)
    conn.commit()

print(f"imported {len(prepared)} matchups")

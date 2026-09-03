import csv
import os
import pathlib
import re
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()

path = pathlib.Path(sys.argv[1])
season = int(sys.argv[2])
url = os.environ.get("DATABASE_URL_DIRECT") or os.environ["DATABASE_URL"]

POS_FILE = pathlib.Path("data/positions.csv")
extra = {}
if POS_FILE.exists():
    for row in csv.DictReader(POS_FILE.open(encoding="utf-8-sig")):
        extra[row["player_name"].strip().lower()] = row["position"].strip().upper()


def clean(s):
    s = "".join(ch for ch in s if not (0xE000 <= ord(ch) <= 0xF8FF))
    return s.replace(chr(8217), chr(39)).strip()


picks, rnd = [], None
for raw in path.read_text(encoding="utf-8").splitlines():
    line = clean(raw)
    if not line:
        continue
    m = re.match(r"^Round (\d+)$", line)
    if m:
        rnd = int(m.group(1))
        continue
    m = re.match(r"^(\d+)\.\s*(.+)$", line)
    if m and rnd:
        parts = re.split(r"\t", m.group(2))
        if len(parts) < 2:
            parts = re.split(r"\s{2,}", m.group(2))
        picks.append((rnd, int(m.group(1)),
                      clean(parts[0]), clean(parts[-1])))

rounds = sorted({r for r, _, _, _ in picks})
print(f"parsed {len(picks)} picks, rounds {rounds[0]}-{rounds[-1]}")

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("select team_name, team_id from teams where season_year = %s",
                    (season,))
        teams = {n.lower(): tid for n, tid in cur.fetchall()}
        cur.execute("select lower(full_name), position, player_id from players")
        known = {n: (p, pid) for n, p, pid in cur.fetchall()}

    bad_team = sorted({t for _, _, _, t in picks if t.lower() not in teams})
    no_pos = sorted({p for _, _, p, _ in picks
                     if p.lower() not in known and p.lower() not in extra})

    if bad_team or no_pos:
        if bad_team:
            print(f"\nteams not found for {season}:")
            for t in bad_team:
                print("  " + t)
        if no_pos:
            print(f"\n{len(no_pos)} players need a position. Add them to "
                  f"data/positions.csv as player_name,position:")
            for p in no_pos:
                print(f"  {p},")
        raise SystemExit(1)

    with conn.cursor() as cur:
        for _, _, p, _ in picks:
            if p.lower() not in known:
                cur.execute("""
                    insert into players (full_name, position) values (%s, %s)
                    on conflict (lower(full_name), position) do nothing
                """, (p, extra[p.lower()]))
        conn.commit()
        cur.execute("select lower(full_name), player_id from players")
        pid = {}
        for n, i in cur.fetchall():
            pid.setdefault(n, i)

        cur.execute("delete from draft_picks where season_year = %s", (season,))
        cur.executemany("""
            insert into draft_picks
                (season_year, round, pick_in_round, team_id, player_id)
            values (%s, %s, %s, %s, %s)
        """, [(season, r, p, teams[t.lower()], pid[pl.lower()])
              for r, p, pl, t in picks])
    conn.commit()

print(f"imported {len(picks)} picks for {season}")

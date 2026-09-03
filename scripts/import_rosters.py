import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()

NEW_POSITIONS = {
    "Aaron Rodgers": "QB", "Adonai Mitchell": "WR", "Alec Pierce": "WR",
    "Audric Estime": "RB", "Bam Knight": "RB", "Bhayshul Tuten": "RB",
    "Browns": "DEF", "Cedric Tillman": "WR", "Chris Rodriguez Jr.": "RB",
    "Christian Watson": "WR", "Dallas Goedert": "TE", "Dylan Sampson": "RB",
    "Elic Ayomanor": "WR", "Emanuel Wilson": "RB", "Falcons": "DEF",
    "Giants": "DEF", "Harold Fannin Jr.": "TE", "Jacoby Brissett": "QB",
    "Jaguars": "DEF", "Jake Ferguson": "TE", "Jawhar Jordan": "RB",
    "Juwan Johnson": "TE", "Kareem Hunt": "RB", "Kayshon Boutte": "WR",
    "Kenny Gainwell": "RB", "Kimani Vidal": "RB", "Kirk Cousins": "QB",
    "Kyle Monangai": "RB", "Lions": "DEF", "Malik Willis": "QB",
    "Marcus Mariota": "QB", "Michael Carter": "RB", "Michael Wilson": "WR",
    "Packers": "DEF", "Parker Washington": "WR", "Patriots": "DEF",
    "Philip Rivers": "QB", "Quentin Johnston": "WR", "Quinn Ewers": "QB",
    "Quinshon Judkins": "RB", "Rhamondre Stevenson": "RB", "Rico Dowdle": "RB",
    "Romeo Doubs": "WR", "Saints": "DEF", "Sam LaPorta": "TE",
    "Seahawks": "DEF", "Shedeur Sanders": "QB", "Tre Tucker": "WR",
    "Troy Franklin": "WR", "Tucker Kraft": "TE", "Tyjae Spears": "RB",
    "Tyler Shough": "QB", "Tyler Warren": "TE", "Woody Marks": "RB",
}

SLOTS = {"QB", "WR", "RB", "TE", "W/T", "W/R/T", "Q/W/R/T", "DEF", "BN", "IR", "K"}

path = pathlib.Path(sys.argv[1])
season = int(sys.argv[2])
url = os.environ.get("DATABASE_URL_DIRECT") or os.environ["DATABASE_URL"]


def clean(s):
    s = "".join(ch for ch in s if not (0xE000 <= ord(ch) <= 0xF8FF))
    return s.replace("\u2019", chr(39)).strip()


with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("select team_name from teams where season_year = %s", (season,))
        team_names = {r[0] for r in cur.fetchall()}

lines = [clean(l) for l in path.read_text(encoding="utf-8").splitlines()]
roster, team, i = [], None, 0
while i < len(lines):
    l = lines[i].strip()
    if l in team_names:
        team = l
        i += 1
        continue
    if l in SLOTS:
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines) and team:
            roster.append((team, lines[i].strip()))
            i += 1
        continue
    i += 1

print(f"parsed {len(roster)} roster entries across "
      f"{len(set(t for t, _ in roster))} teams")

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("select lower(full_name), player_id from players")
        players = dict(cur.fetchall())

    unknown = sorted({n for _, n in roster
                      if n.lower() not in players and n not in NEW_POSITIONS})
    if unknown:
        print("aborted, these players have no position:")
        for u in unknown:
            print("  " + u)
        raise SystemExit(1)

    with conn.cursor() as cur:
        for _, n in roster:
            if n.lower() not in players:
                cur.execute("""
                    insert into players (full_name, position) values (%s, %s)
                    on conflict (lower(full_name), position) do nothing
                """, (n, NEW_POSITIONS[n]))
        conn.commit()
        cur.execute("select lower(full_name), player_id from players")
        players = dict(cur.fetchall())

        cur.execute("select team_name, team_id from teams where season_year = %s",
                    (season,))
        teams = dict(cur.fetchall())

        cur.execute("delete from rosters where season_year = %s", (season,))
        cur.executemany("""
            insert into rosters (season_year, team_id, player_id, acquired)
            values (%s, %s, %s, null)
        """, [(season, teams[t], players[n.lower()]) for t, n in roster])
    conn.commit()

print(f"imported {len(roster)} roster spots for {season}")


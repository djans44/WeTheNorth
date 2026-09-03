import os
import pathlib
import re
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()

path = pathlib.Path(sys.argv[1])
season = int(sys.argv[2])
source = "FantasyPros 2QB half 12tm"
captured = "2026-09-03"
url = os.environ.get("DATABASE_URL_DIRECT") or os.environ["DATABASE_URL"]

SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm(name):
    s = name.lower().replace(chr(8217), chr(39))
    s = re.sub(r"[.`,]", "", s).replace(chr(39), "")
    s = SUFFIX.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


rows = []
for line in path.read_text(encoding="utf-8").splitlines():
    p = line.split("\t")
    if len(p) < 3 or not re.match(r"^(QB|RB|WR|TE|K|DST)\d+$", p[0].strip()):
        continue
    rows.append((p[2].strip(), int(p[1].strip()), p[0].strip()))

print(f"parsed {len(rows)} rows")
print("  first:", rows[0], "  last:", rows[-1])

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("select player_id, full_name, position from players")
        by_norm, by_last = {}, {}
        for pid, name, pos in cur.fetchall():
            by_norm.setdefault(norm(name), pid)
            if pos == "DEF":
                by_last.setdefault(norm(name).split()[-1], pid)

    matched, missed = [], []
    for name, pick, rank in rows:
        pid = by_norm.get(norm(name))
        if pid is None and rank.startswith("DST"):
            pid = by_last.get(norm(name).split()[-1])
        if pid is None:
            missed.append(name)
        else:
            matched.append((season, pid, pick, source, captured))

    with conn.cursor() as cur:
        cur.executemany("""
            insert into player_adp (season_year, player_id, adp, source, captured_on)
            values (%s, %s, %s, %s, %s)
            on conflict (season_year, player_id) do update set
                adp = excluded.adp, source = excluded.source,
                captured_on = excluded.captured_on
        """, matched)
    conn.commit()

print(f"imported {len(matched)}, skipped {len(missed)}")
for m in missed[:40]:
    print("  skipped: " + m)

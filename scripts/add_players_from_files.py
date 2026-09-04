import os
import pathlib
import re
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()

PLAYER = re.compile(r"^(.+?)\s+([A-Za-z]{2,3})\s+-\s+(QB|RB|WR|TE|K|DEF)(\s+.*)?$")
url = os.environ.get("DATABASE_URL_DIRECT") or os.environ["DATABASE_URL"]


def clean(s):
    s = "".join(ch for ch in s if not (0xE000 <= ord(ch) <= 0xF8FF))
    return s.replace(chr(8217), chr(39)).strip()


found = {}
for arg in sys.argv[1:]:
    p = pathlib.Path(arg)
    if not p.exists():
        print(f"missing file: {p}")
        continue
    for raw in p.read_text(encoding="utf-8").replace("\r", "").split("\n"):
        m = PLAYER.match(clean(raw))
        if m:
            found.setdefault(m.group(1).strip(), m.group(3))

print(f"{len(found)} distinct players in the files")

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("select lower(full_name) from players")
        known = {r[0] for r in cur.fetchall()}

        added = 0
        for name, pos in sorted(found.items()):
            if name.lower() in known:
                continue
            cur.execute("""
                insert into players (full_name, position) values (%s, %s)
                on conflict (lower(full_name), position) do nothing
            """, (name, pos))
            print(f"  + {name} ({pos})")
            added += 1
    conn.commit()

print(f"added {added} players")

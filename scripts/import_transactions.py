import collections
import datetime as dt
import os
import pathlib
import re
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()

season = int(sys.argv[1])
waivers = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None
trades = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else None
url = os.environ.get("DATABASE_URL_DIRECT") or os.environ["DATABASE_URL"]

SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")
PLAYER = re.compile(r"^(.+?)\s+([A-Za-z]{2,3})\s+-\s+(QB|RB|WR|TE|K|DEF)(\s+.*)?$")
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def clean(s):
    s = "".join(ch for ch in s if not (0xE000 <= ord(ch) <= 0xF8FF))
    return s.replace(chr(8217), chr(39)).strip()


def strip_paren(t):
    return re.sub(r"\s*\(\s*.*?\s*\)\s*$", "", t).strip()


def norm(name):
    s = name.lower().replace(chr(8217), chr(39))
    s = re.sub(r"[.`,]", "", s).replace(chr(39), "")
    s = SUFFIX.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(text):
    m = re.match(r"^([A-Z][a-z]{2})\s+(\d{1,2})", text.strip())
    if not m:
        return None
    mon = MONTHS.get(m.group(1))
    if not mon:
        return None
    year = season if mon >= 8 else season + 1
    return dt.date(year, mon, int(m.group(2)))


def read(path):
    return [clean(l) for l in path.read_text(encoding="utf-8")
            .replace("\r", "").split("\n")]


adds, moves = [], []

if waivers and waivers.exists():
    lines = read(waivers)
    i = 0
    while i < len(lines):
        m = PLAYER.match(lines[i])
        if m and i + 3 < len(lines):
            kind_text = lines[i + 1]
            team = strip_paren(lines[i + 2])
            date = lines[i + 3]
            faab = None
            low = kind_text.lower()
            if low.startswith("$"):
                method = "faab"
                faab = re.sub(r"[^0-9.]", "", kind_text.split()[0])
            elif "commissioner" in low:
                method = "commissioner"
            elif low.startswith("waiver"):
                method = "waiver"
            else:
                method = "free_agent"
            adds.append((m.group(1).strip(), team, method, faab, date))
            i += 4
            continue
        i += 1

if trades and trades.exists():
    lines = read(trades)
    blocks, cur, i = [], [], 0
    while i < len(lines):
        if lines[i] == "Traded to" and i + 2 < len(lines):
            blocks.append({"players": cur,
                           "team": strip_paren(lines[i + 1]),
                           "date": lines[i + 2]})
            cur = []
            i += 3
            continue
        m = PLAYER.match(lines[i])
        if m:
            cur.append(m.group(1).strip())
        i += 1

    grouped = collections.defaultdict(list)
    for b in blocks:
        grouped[b["date"]].append(b)
    for date, sides in grouped.items():
        if len(sides) != 2:
            print(f"skipping trade on {date}: {len(sides)} sides, expected 2")
            continue
        a, b = sides
        for player in a["players"]:
            moves.append((player, a["team"], b["team"], date))
        for player in b["players"]:
            moves.append((player, b["team"], a["team"], date))

print(f"parsed {len(adds)} adds and {len(moves)} traded players")

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("select team_name, team_id from teams where season_year = %s",
                    (season,))
        teams = {norm(n): tid for n, tid in cur.fetchall()}
        cur.execute("select lower(full_name), player_id from players")
        players = {}
        for n, pid in cur.fetchall():
            players.setdefault(norm(n), pid)

    problems = []
    for name, team, *_ in adds:
        if norm(team) not in teams:
            problems.append(f"unknown team: {team}")
        if norm(name) not in players:
            problems.append(f"unknown player: {name}")
    for name, to_t, from_t, _ in moves:
        for t in (to_t, from_t):
            if norm(t) not in teams:
                problems.append(f"unknown team: {t}")
        if norm(name) not in players:
            problems.append(f"unknown player: {name}")

    if problems:
        print("aborted, nothing written:")
        for p in sorted(set(problems)):
            print("  " + p)
        raise SystemExit(1)

    with conn.cursor() as cur:
        cur.execute("delete from transactions where season_year = %s", (season,))
        cur.executemany("""
            insert into transactions
                (season_year, kind, method, faab_amount, player_id,
                 to_team_id, occurred_on, occurred_raw)
            values (%s, 'add', %s, %s, %s, %s, %s, %s)
        """, [(season, method, faab, players[norm(name)], teams[norm(team)],
               parse_date(date), date) for name, team, method, faab, date in adds])

        cur.executemany("""
            insert into transactions
                (season_year, kind, player_id, to_team_id, from_team_id,
                 occurred_on, occurred_raw)
            values (%s, 'trade', %s, %s, %s, %s, %s)
        """, [(season, players[norm(name)], teams[norm(to_t)],
               teams[norm(from_t)], parse_date(date), date)
              for name, to_t, from_t, date in moves])

        cur.execute("""
            update rosters r
            set acquired = coalesce((
                select case when t.kind = 'trade' then 'trade' else 'waiver' end
                from transactions t
                where t.season_year = r.season_year
                  and t.player_id   = r.player_id
                  and t.to_team_id  = r.team_id
                order by t.occurred_on desc nulls last, t.transaction_id desc
                limit 1
            ), case when exists (
                select 1 from draft_picks d
                where d.season_year = r.season_year
                  and d.player_id   = r.player_id
                  and d.team_id     = r.team_id
            ) then 'draft' end)
            where r.season_year = %s
        """, (season,))
    conn.commit()

print(f"imported {len(adds) + len(moves)} transactions for {season}")

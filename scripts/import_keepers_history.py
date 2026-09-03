import os
import re

import psycopg
from dotenv import load_dotenv

load_dotenv()
url = os.environ.get("DATABASE_URL_DIRECT") or os.environ["DATABASE_URL"]
CURRENT = 2026

RAW = """2023|Trevor Lawrence|5|||
2023|Saquon Barkley|3|||
2023|Christian McCaffrey|1|||
2023|DJ Moore|5|||
2023|Tua Tagovailoa|4|||
2023|Kyle Pitts|13|||
2023|Joe Burrow|1|||
2023|Ja'Marr Chase|2|||
2023|Davante Adams|3|||
2023|Austin Ekeler|1|||
2023|Calvin Ridley|13|||
2023|Tyreek Hill|4|||
2023|Deshaun Watson|10|||
2023|Lamar Jackson|1|||
2023|Amari Cooper|9|||
2023|Derrick Henry|2|||
2023|Justin Fields|13|||
2023|DeVonta Smith|10|||
2023|Joe Mixon|2|||
2023|Cooper Kupp|1|||
2023|Justin Jefferson|1|||
2023|Stefon Diggs|2|||
2023|Jaylen Waddle|7|||
2023|Dak Prescott|2|||
2023|Drake London|7|||
2023|A.J. Brown|3|||
2023|Garrett Wilson|13|||
2023|Jalen Hurts|1|||
2023|Josh Allen|1|||
2023|CeeDee Lamb|2|||
2023|Nick Chubb|4|||
2023|Patrick Mahomes|1|||
2023|Amon-Ra St. Brown|8|||
2023|Chris Olave|10|||
2024|Christian McCaffrey|1|Y|2024|
2024|Puka Nacua|13|||
2024|Kyler Murray|10|||
2024|Tua Tagovailoa|4|Y|2026|4
2024|DJ Moore|5|Y|2026|5
2024|Bijan Robinson|1|||
2024|Ja'Marr Chase|2|Y|2026|2
2024|Kyren Williams|13|||
2024|Joe Burrow|1|Y|2026|1
2024|C.J. Stroud|10|||
2024|CeeDee Lamb|2|Y|2026|2
2024|Breece Hall|5|||
2024|Lamar Jackson|1|Y|2026|1
2024|Travis Etienne Jr.|2|||
2024|Tyreek Hill|4|Y|2026|3
2024|Derrick Henry|2|Y|2024|
2024|Nico Collins|10|||
2024|Aaron Rodgers|13|||
2024|Brock Purdy|4|||
2024|DeVonta Smith|10|Y|2026|8
2024|Matthew Stafford|6|||
2024|Patrick Mahomes|1|Y|2026|1
2024|De'Von Achane|13|||
2024|Jaylen Waddle|7|Y|2024|
2024|Dak Prescott|2|Y|2026|2
2024|Drake London|7|Y|2026|6
2024|Josh Jacobs|3|||
2024|Jalen Hurts|1|Y|2026|1
2024|A.J. Brown|3|Y|2024|
2024|Anthony Richardson Sr.|2|||
2024|Josh Allen|1|Y|2026|1
2024|Isiah Pacheco|8|||
2024|Justin Jefferson|1|Y|2026|1
2024|Amon-Ra St. Brown|8|Y|2026|4
2024|Chris Olave|10|Y|2024|"""

SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm(name):
    s = name.lower().replace(chr(8217), chr(39))
    s = re.sub(r"[.'`,]", "", s)
    s = SUFFIX.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


rows = []
for line in RAW.strip().splitlines():
    p = line.split("|")
    rows.append({"y": int(p[0]), "pl": p[1], "rd": int(p[2]),
                 "k": p[3] == "Y",
                 "exp": int(p[4]) if p[4] else None,
                 "cr": int(p[5]) if p[5] else None})

first_seen = {}
for r in sorted(rows, key=lambda x: x["y"]):
    first_seen.setdefault(norm(r["pl"]), r)

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            select d.season_year, d.round, p.full_name, d.team_id, t.owner_id, p.player_id
            from draft_picks d
            join players p on p.player_id = d.player_id
            join teams   t on t.team_id   = d.team_id
            where d.season_year in (2023, 2024)
        """)
        board = {}
        for y, rd, name, tid, oid, pid in cur.fetchall():
            board[(y, norm(name), rd)] = (tid, oid, pid)

    missing = [r for r in rows if (r["y"], norm(r["pl"]), r["rd"]) not in board]
    if missing:
        print(f"{len(missing)} keepers not found on the draft board:")
        for r in missing:
            print(f"  {r['y']} {r['pl']} round {r['rd']}")
        raise SystemExit(1)

    with conn.cursor() as cur:
        for season in (2023, 2024):
            cur.execute("delete from keeper_selections where season_year = %s",
                        (season,))

        for r in rows:
            tid, oid, pid = board[(r["y"], norm(r["pl"]), r["rd"])]
            key = norm(r["pl"])
            years = sorted({x["y"] for x in rows if norm(x["pl"]) == key})
            kyear = years.index(r["y"]) + 1
            original = first_seen[key]["rd"]
            contract_id = None

            if r["k"]:
                signed = r["y"]
                years_len = 1 if r["exp"] == signed else 3
                final = signed + years_len - 1
                status = "expired" if final < CURRENT else "active"
                cur.execute("""
                    insert into keeper_contracts
                        (player_id, owner_id, original_round, signed_season,
                         contract_years, contract_round, status)
                    values (%s, %s, %s, %s, %s, %s, %s)
                    on conflict (player_id, signed_season) do update set
                        owner_id       = excluded.owner_id,
                        original_round = excluded.original_round,
                        contract_years = excluded.contract_years,
                        contract_round = excluded.contract_round,
                        status         = excluded.status,
                        updated_at     = now()
                    returning contract_id
                """, (pid, oid, original, signed, years_len, r["cr"], status))
                contract_id = cur.fetchone()[0]

            cur.execute("""
                insert into keeper_selections
                    (season_year, team_id, player_id, cost_round,
                     keeper_year, contract_id)
                values (%s, %s, %s, %s, %s, %s)
            """, (r["y"], tid, pid, r["rd"], kyear, contract_id))

        for season in (2023, 2024):
            cur.execute("""
                update draft_picks d set is_keeper = true
                from keeper_selections k
                where k.season_year = d.season_year
                  and k.player_id   = d.player_id
                  and d.season_year = %s
            """, (season,))
    conn.commit()

print(f"imported {len(rows)} keeper selections for 2023 and 2024")


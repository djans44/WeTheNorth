import csv
import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    with conn.cursor() as cur:
        cur.execute("select full_name, position from players order by full_name")
        rows = cur.fetchall()

with open("data/positions.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["player_name", "position"])
    w.writerows(rows)

print(f"wrote {len(rows)} players to data/positions.csv")

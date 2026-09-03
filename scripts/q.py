import os, sys
import psycopg
from dotenv import load_dotenv

load_dotenv()
sql = " ".join(sys.argv[1:])
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            print(row)

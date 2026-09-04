import os, sys
import psycopg
from dotenv import load_dotenv

load_dotenv()
sql = " ".join(sys.argv[1:])
url = os.environ.get("DATABASE_URL_DIRECT") or os.environ["DATABASE_URL"]

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute(sql)
        if cur.description:
            for row in cur.fetchall():
                print(row)
        else:
            print(cur.statusmessage)
    conn.commit()

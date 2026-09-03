import os
from dotenv import load_dotenv
import psycopg

load_dotenv()
url = os.environ["DATABASE_URL"]

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("select version()")
        print(cur.fetchone()[0])

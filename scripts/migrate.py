import os
import pathlib

import psycopg
from dotenv import load_dotenv

load_dotenv()

url = os.environ.get("DATABASE_URL_DIRECT") or os.environ["DATABASE_URL"]
migrations_dir = pathlib.Path("sql/migrations")

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            create table if not exists schema_migrations (
                filename   text primary key,
                applied_at timestamptz not null default now()
            )
        """)
        cur.execute("select filename from schema_migrations")
        applied = {row[0] for row in cur.fetchall()}
    conn.commit()

    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in applied:
            continue
        print(f"applying {path.name}")
        sql = path.read_text(encoding="utf-8-sig")
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "insert into schema_migrations (filename) values (%s)",
                (path.name,),
            )
        conn.commit()

print("migrations up to date")

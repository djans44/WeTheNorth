import os
import pathlib

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.rows import dict_row

load_dotenv()

STATIC_DIR = pathlib.Path("app/static")

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory="app/templates")


def static_url(path: str) -> str:
    try:
        stamp = int((STATIC_DIR / path).stat().st_mtime)
    except OSError:
        stamp = 0
    return f"/static/{path}?v={stamp}"


templates.env.globals["static_url"] = static_url


def get_db():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def query(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    with get_db() as conn:
        seasons = query(conn, """
            select * from season_results
            where champion is not null
            order by season_year desc
        """)

        standings = query(conn, """
            select * from owner_all_time_stats
            where seasons_played > 0
            order by win_pct desc, points_for desc
        """)

        h2h_rows = query(conn, "select * from owner_head_to_head")

        projections = query(conn, """
            select * from owner_projection_stats
            order by avg_vs_projection desc
        """)

        high = query(conn, """
            select username, team_name, season_year, week, points_for
            from game_log order by points_for desc limit 5
        """)
        low = query(conn, """
            select username, team_name, season_year, week, points_for
            from game_log order by points_for asc limit 5
        """)
        blowouts = query(conn, """
            select username, opponent_username, season_year, week,
                   points_for, points_against,
                   points_for - points_against as margin
            from game_log where result = 'W'
            order by margin desc limit 5
        """)
        nailbiters = query(conn, """
            select username, opponent_username, season_year, week,
                   points_for, points_against,
                   points_for - points_against as margin
            from game_log where result = 'W'
            order by margin asc limit 5
        """)
        shootouts = query(conn, """
            select username, opponent_username, season_year, week,
                   points_for + points_against as combined
            from game_log where result = 'W'
            order by combined desc limit 5
        """)

    order = [s["username"] for s in standings]
    grid = {}
    for r in h2h_rows:
        grid[(r["username"], r["opponent_username"])] = r

    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context={
            "seasons": seasons,
            "standings": standings,
            "order": order,
            "grid": grid,
            "projections": projections,
            "high": high,
            "low": low,
            "blowouts": blowouts,
            "nailbiters": nailbiters,
            "shootouts": shootouts,
        },
    )


@app.post("/login")
def login():
    # Placeholder: accepts anything and lets you in. Real auth comes later.
    return RedirectResponse(url="/history", status_code=303)


@app.get("/health")
def health():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1")
            cur.fetchone()
    return {"status": "ok", "database": "connected"}


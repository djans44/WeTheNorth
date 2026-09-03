import os
import pathlib
import time

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
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

_owner_cache = {"at": 0.0, "rows": []}


def nav_owners():
    """Managers for the nav dropdown. Cached, since owners rarely change."""
    now = time.time()
    if now - _owner_cache["at"] > 300:
        try:
            with get_db() as conn:
                _owner_cache["rows"] = query(conn, """
                    select username from owner_all_time_stats
                    where seasons_played > 0
                    order by username
                """)
                _owner_cache["at"] = now
        except Exception:
            pass
    return _owner_cache["rows"]


templates.env.globals["nav_owners"] = nav_owners


def get_db():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def query(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/login")
def login():
    return RedirectResponse(url="/history", status_code=303)


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
            from game_log where result = 'W' order by margin desc limit 5
        """)
        nailbiters = query(conn, """
            select username, opponent_username, season_year, week,
                   points_for, points_against,
                   points_for - points_against as margin
            from game_log where result = 'W' order by margin asc limit 5
        """)
        shootouts = query(conn, """
            select username, opponent_username, season_year, week,
                   points_for + points_against as combined
            from game_log where result = 'W' order by combined desc limit 5
        """)

    order = [s["username"] for s in standings]
    grid = {(r["username"], r["opponent_username"]): r for r in h2h_rows}

    return templates.TemplateResponse(
        request=request, name="history.html",
        context={"seasons": seasons, "standings": standings, "order": order,
                 "grid": grid, "projections": projections, "high": high,
                 "low": low, "blowouts": blowouts, "nailbiters": nailbiters,
                 "shootouts": shootouts},
    )


@app.get("/teams", response_class=HTMLResponse)
def teams(request: Request):
    with get_db() as conn:
        owners = query(conn, """
            select o.*, ow.is_retired
            from owner_all_time_stats o
            join owners ow on ow.owner_id = o.owner_id
            where o.seasons_played > 0
            order by o.win_pct desc, o.points_for desc
        """)
    return templates.TemplateResponse(
        request=request, name="teams.html", context={"owners": owners},
    )


@app.get("/team/{name}", response_class=HTMLResponse)
def team(request: Request, name: str):
    with get_db() as conn:
        rows = query(conn, """
            select o.*, ow.is_retired
            from owner_all_time_stats o
            join owners ow on ow.owner_id = o.owner_id
            where lower(o.username) = lower(%s)
        """, (name,))
        if not rows:
            raise HTTPException(status_code=404, detail="No such owner")
        owner = rows[0]
        oid = owner["owner_id"]

        seasons = query(conn, """
            select
                s.season_year, s.team_name, s.wins, s.losses, s.ties,
                s.points_for, s.points_against, s.made_playoffs,
                case
                    when sr.champion_owner_id  = %s then 'Champion'
                    when sr.runner_up_owner_id = %s then 'Runner-up'
                    when sr.third_owner_id     = %s then 'Third'
                    when s.made_playoffs            then 'Playoffs'
                    else ''
                end as finish
            from team_season_stats s
            left join season_results sr on sr.season_year = s.season_year
            where s.owner_id = %s and s.games_played > 0
            order by s.season_year desc
        """, (oid, oid, oid, oid))

        h2h = query(conn, """
            select * from owner_head_to_head
            where owner_id = %s
            order by wins - losses desc, opponent_username
        """, (oid,))

        best = query(conn, """
            select season_year, week, points_for, opponent_username, result
            from game_log where owner_id = %s
            order by points_for desc limit 3
        """, (oid,))
        worst = query(conn, """
            select season_year, week, points_for, opponent_username, result
            from game_log where owner_id = %s
            order by points_for asc limit 3
        """, (oid,))

        proj_rows = query(conn, """
            select * from owner_projection_stats where owner_id = %s
        """, (oid,))
        projection = proj_rows[0] if proj_rows else None

    return templates.TemplateResponse(
        request=request, name="team.html",
        context={"owner": owner, "seasons": seasons, "h2h": h2h,
                 "best": best, "worst": worst, "projection": projection},
    )


@app.get("/health")
def health():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1")
            cur.fetchone()
    return {"status": "ok", "database": "connected"}


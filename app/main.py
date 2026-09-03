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
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

STATIC_DIR = pathlib.Path("app/static")
PUBLIC_PATHS = {"/", "/login", "/logout", "/health"}

app = FastAPI()


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)
    if not request.session.get("owner_id"):
        return RedirectResponse(url="/", status_code=303)
    return await call_next(request)


# Added after the middleware above so it sits outside it in the stack,
# which is what makes request.session available inside it.
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "dev-only-insecure-key"),
    max_age=60 * 60 * 8,
    same_site="lax",
)

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


_owner_cache = {"at": 0.0, "rows": []}


def nav_owners():
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

_season_cache = {"at": 0.0, "rows": []}


def nav_seasons():
    now = time.time()
    if now - _season_cache["at"] > 300:
        try:
            with get_db() as conn:
                _season_cache["rows"] = query(conn, """
                    select season_year from seasons order by season_year desc
                """)
                _season_cache["at"] = now
        except Exception:
            pass
    return _season_cache["rows"]


templates.env.globals["nav_seasons"] = nav_seasons


@app.get("/", response_class=HTMLResponse)
def home(request: Request, error: int = 0):
    if request.session.get("owner_id"):
        return RedirectResponse(url="/current", status_code=303)
    return templates.TemplateResponse(
        request=request, name="index.html", context={"error": error},
    )


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    email = (form.get("email") or "").strip()

    with get_db() as conn:
        rows = query(conn, """
            select owner_id, username, is_admin
            from owners
            where email is not null and lower(email) = lower(%s)
        """, (email,))

    if not rows:
        return RedirectResponse(url="/?error=1", status_code=303)

    request.session["owner_id"] = rows[0]["owner_id"]
    request.session["username"] = rows[0]["username"]
    request.session["is_admin"] = rows[0]["is_admin"]
    return RedirectResponse(url="/current", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


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


@app.get("/seasons", response_class=HTMLResponse)
def seasons_index(request: Request):
    with get_db() as conn:
        rows = query(conn, """
            select s.season_year, s.is_complete, s.team_count, s.keeper_count,
                   sr.champion, sr.champion_team, sr.runner_up,
                   sr.regular_season_leader, sr.leader_wins, sr.leader_losses,
                   (select count(*) from matchups m
                     where m.season_year = s.season_year
                       and m.team_a_points is not null) as games
            from seasons s
            left join season_results sr on sr.season_year = s.season_year
            order by s.season_year desc
        """)
    return templates.TemplateResponse(
        request=request, name="seasons.html", context={"seasons": rows},
    )


@app.get("/current")
def current_season():
    with get_db() as conn:
        rows = query(conn, "select max(season_year) as y from seasons")
    return RedirectResponse(url=f"/season/{rows[0]['y']}", status_code=307)


@app.get("/season/{year}", response_class=HTMLResponse)
def season(request: Request, year: int):
    with get_db() as conn:
        head = query(conn, """
            select s.*, sr.champion, sr.champion_team, sr.runner_up,
                   sr.runner_up_team, sr.third_place, sr.regular_season_leader
            from seasons s
            left join season_results sr on sr.season_year = s.season_year
            where s.season_year = %s
        """, (year,))
        if not head:
            raise HTTPException(status_code=404, detail="No such season")

        standings = query(conn, """
            select * from team_season_stats
            where season_year = %s
            order by games_played = 0, wins desc, points_for desc
        """, (year,))

        games = query(conn, """
            select m.week, m.game_type,
                   ta.team_name as team_a, oa.username as owner_a,
                   m.team_a_points as points_a, m.team_a_projected as proj_a,
                   tb.team_name as team_b, ob.username as owner_b,
                   m.team_b_points as points_b, m.team_b_projected as proj_b
            from matchups m
            join teams  ta on ta.team_id  = m.team_a_id
            join owners oa on oa.owner_id = ta.owner_id
            left join teams  tb on tb.team_id  = m.team_b_id
            left join owners ob on ob.owner_id = tb.owner_id
            where m.season_year = %s
            order by m.week, m.game_type, m.matchup_id
        """, (year,))

        records = query(conn, """
            select username, week, points_for, opponent_username
            from game_log where season_year = %s
            order by points_for desc limit 3
        """, (year,))

    weeks = []
    for g in games:
        if not weeks or weeks[-1]["week"] != g["week"]:
            weeks.append({"week": g["week"], "games": []})
        weeks[-1]["games"].append(g)

    return templates.TemplateResponse(
        request=request, name="season.html",
        context={"s": head[0], "standings": standings,
                 "weeks": weeks, "records": records},
    )


@app.get("/health")
def health():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1")
            cur.fetchone()
    return {"status": "ok", "database": "connected"}

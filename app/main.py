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


def ordinal(n):
    if n is None:
        return ""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


templates.env.globals["ordinal"] = ordinal


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
                s.points_for, s.points_against, s.made_playoffs, s.final_rank,
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
            order by final_rank nulls last, wins desc, points_for desc
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


LAYOUTS = {
    "regular":      ["regular"] * 6,
    "quarterfinal": ["quarterfinal"] * 4 + ["consolation"] * 4,
    "semifinal":    ["semifinal"] * 2 + ["fifth_place"] + ["consolation"] * 2 + ["eleventh_place"],
    "final":        ["championship", "third_place", "seventh_place", "ninth_place"],
}

MODE_LABELS = {
    "regular": "Regular season",
    "quarterfinal": "Quarterfinals",
    "semifinal": "Semifinals",
    "final": "Final",
}

WEEK_MODES = {w: {15: "quarterfinal", 16: "semifinal", 17: "final"}.get(w, "regular")
              for w in range(1, 18)}


def infer_mode(existing):
    types = {r["game_type"] for r in existing}
    if types & {"championship", "third_place", "seventh_place", "ninth_place"}:
        return "final"
    if types & {"semifinal", "fifth_place", "eleventh_place"}:
        return "semifinal"
    if "quarterfinal" in types:
        return "quarterfinal"
    return "regular"


def build_rows(mode, existing):
    by_type = {}
    for r in existing:
        by_type.setdefault(r["game_type"], []).append(r)
    rows = []
    for gt in LAYOUTS[mode]:
        pool = by_type.get(gt) or []
        r = pool.pop(0) if pool else None
        rows.append({
            "game_type": gt,
            "team_a_id": r["team_a_id"] if r else None,
            "team_b_id": r["team_b_id"] if r else None,
            "pa": r["team_a_points"] if r else None,
            "pb": r["team_b_points"] if r else None,
            "ja": r["team_a_projected"] if r else None,
            "jb": r["team_b_projected"] if r else None,
        })
    return rows


@app.get("/admin/scores", response_class=HTMLResponse)
def admin_scores(request: Request, season: int = 0, week: int = 0,
                 mode: str = "", saved: int = 0):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")

    with get_db() as conn:
        years = query(conn, "select season_year from seasons order by season_year desc")
        if not season:
            season = years[0]["season_year"]

        teams = query(conn, """
            select t.team_id, t.team_name, o.username
            from teams t join owners o on o.owner_id = t.owner_id
            where t.season_year = %s
            order by t.team_name
        """, (season,))

        existing = []
        if week:
            existing = query(conn, """
                select week, game_type, team_a_id, team_b_id,
                       team_a_points, team_b_points,
                       team_a_projected, team_b_projected
                from matchups
                where season_year = %s and week = %s
                order by game_type, matchup_id
            """, (season, week))

        filled = query(conn, """
            select week from matchups where season_year = %s
            group by week order by week
        """, (season,))

    requested = mode if mode in LAYOUTS else ""
    if requested:
        mode = requested
    elif existing:
        mode = infer_mode(existing)
    else:
        mode = WEEK_MODES.get(week, "regular")

    return templates.TemplateResponse(
        request=request, name="admin_scores.html",
        context={"years": years, "season": season, "week": week, "mode": mode,
                 "requested": requested, "week_modes": WEEK_MODES,
                 "teams": teams, "rows": build_rows(mode, existing),
                 "filled": filled, "saved": saved, "errors": [],
                 "labels": MODE_LABELS},
    )


@app.post("/admin/scores")
async def admin_scores_save(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")

    form = await request.form()
    season = int(form["season"])
    week = int(form["week"])
    mode = form.get("mode") if form.get("mode") in LAYOUTS else "regular"
    layout = LAYOUTS[mode]

    def val(name):
        v = (form.get(name) or "").strip()
        return v or None

    rows, seen = [], []
    for i, gt in enumerate(layout):
        a, b = val(f"r{i}_team_a"), val(f"r{i}_team_b")
        row = {"game_type": gt,
               "team_a_id": int(a) if a else None,
               "team_b_id": int(b) if b else None,
               "pa": val(f"r{i}_pa"), "pb": val(f"r{i}_pb"),
               "ja": val(f"r{i}_ja"), "jb": val(f"r{i}_jb")}
        rows.append(row)
        seen += [t for t in (row["team_a_id"], row["team_b_id"]) if t]

    with get_db() as conn:
        years = query(conn, "select season_year from seasons order by season_year desc")
        teams = query(conn, """
            select t.team_id, t.team_name, o.username
            from teams t join owners o on o.owner_id = t.owner_id
            where t.season_year = %s order by t.team_name
        """, (season,))
        filled = query(conn, """
            select week from matchups where season_year = %s
            group by week order by week
        """, (season,))

    names = {t["team_id"]: t["team_name"] for t in teams}
    errors = []

    dupes = {t for t in seen if seen.count(t) > 1}
    for t in sorted(dupes):
        errors.append(f"{names.get(t, t)} appears more than once.")

    for i, row in enumerate(rows, start=1):
        if row["team_b_id"] and not row["team_a_id"]:
            errors.append(f"Row {i} has an opponent but no team.")
        if row["team_a_id"] and row["team_a_id"] == row["team_b_id"]:
            errors.append(f"Row {i} has a team playing itself.")

    if mode in ("regular", "quarterfinal", "semifinal"):
        missing = [n for tid, n in names.items() if tid not in seen]
        if missing:
            errors.append("Not entered: " + ", ".join(sorted(missing)) + ".")

    if errors:
        return templates.TemplateResponse(
            request=request, name="admin_scores.html", status_code=400,
            context={"years": years, "season": season, "week": week, "mode": mode,
                     "requested": mode, "week_modes": WEEK_MODES,
                     "teams": teams, "rows": rows, "filled": filled,
                     "saved": 0, "errors": errors, "labels": MODE_LABELS},
        )

    prepared = []
    for row in rows:
        a_id, b_id = row["team_a_id"], row["team_b_id"]
        if not a_id:
            continue
        a_pts, b_pts, a_proj, b_proj = row["pa"], row["pb"], row["ja"], row["jb"]
        if b_id is None:
            prepared.append((season, week, row["game_type"], a_id, None,
                             a_pts, None, a_proj, None))
            continue
        if a_id > b_id:
            a_id, b_id = b_id, a_id
            a_pts, b_pts = b_pts, a_pts
            a_proj, b_proj = b_proj, a_proj
        prepared.append((season, week, row["game_type"], a_id, b_id,
                         a_pts, b_pts, a_proj, b_proj))

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from matchups where season_year = %s and week = %s",
                        (season, week))
            cur.executemany("""
                insert into matchups
                    (season_year, week, game_type, team_a_id, team_b_id,
                     team_a_points, team_b_points,
                     team_a_projected, team_b_projected)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, prepared)
        conn.commit()

    return RedirectResponse(
        url=f"/admin/scores?season={season}&week={week}&mode={mode}&saved={len(prepared)}",
        status_code=303)


@app.get("/rules", response_class=HTMLResponse)
def rules(request: Request):
    return templates.TemplateResponse(request=request, name="rules.html")


@app.get("/health")
def health():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1")
            cur.fetchone()
    return {"status": "ok", "database": "connected"}









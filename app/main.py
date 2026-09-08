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


def ordinal(n):
    if n is None:
        return ""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


templates.env.globals["static_url"] = static_url
templates.env.globals["ordinal"] = ordinal


def get_db():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def query(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


_owner_cache = {"at": 0.0, "rows": []}
_season_cache = {"at": 0.0, "rows": []}


def nav_owners():
    now = time.time()
    if now - _owner_cache["at"] > 300:
        try:
            with get_db() as conn:
                _owner_cache["rows"] = query(conn, """
                    select username from owner_all_time_stats
                    where seasons_played > 0 order by username
                """)
                _owner_cache["at"] = now
        except Exception:
            pass
    return _owner_cache["rows"]


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


templates.env.globals["nav_owners"] = nav_owners
templates.env.globals["nav_seasons"] = nav_seasons


@app.get("/", response_class=HTMLResponse)
def home(request: Request, error: int = 0):
    if request.session.get("owner_id"):
        return RedirectResponse(url="/current", status_code=303)
    return templates.TemplateResponse(
        request=request, name="index.html", context={"error": error})


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    email = (form.get("email") or "").strip()
    with get_db() as conn:
        rows = query(conn, """
            select owner_id, username, is_admin from owners
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
            select * from season_results where champion is not null
            order by season_year desc
        """)
        standings = query(conn, """
            select * from owner_all_time_stats where seasons_played > 0
            order by win_pct desc, points_for desc
        """)
        h2h_rows = query(conn, "select * from owner_head_to_head")
        projections = query(conn, """
            select * from owner_projection_stats order by avg_vs_projection desc
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
                   points_for, points_against, points_for - points_against as margin
            from game_log where result = 'W' order by margin desc limit 5
        """)
        nailbiters = query(conn, """
            select username, opponent_username, season_year, week,
                   points_for, points_against, points_for - points_against as margin
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
                 "shootouts": shootouts})


@app.get("/teams", response_class=HTMLResponse)
def teams(request: Request):
    with get_db() as conn:
        owners = query(conn, """
            select o.*, ow.is_retired from owner_all_time_stats o
            join owners ow on ow.owner_id = o.owner_id
            where o.seasons_played > 0
            order by o.win_pct desc, o.points_for desc
        """)
    return templates.TemplateResponse(
        request=request, name="teams.html", context={"owners": owners})


@app.get("/team/{name}", response_class=HTMLResponse)
def team(request: Request, name: str):
    with get_db() as conn:
        rows = query(conn, """
            select o.*, ow.is_retired from owner_all_time_stats o
            join owners ow on ow.owner_id = o.owner_id
            where lower(o.username) = lower(%s)
        """, (name,))
        if not rows:
            raise HTTPException(status_code=404, detail="No such owner")
        owner = rows[0]
        oid = owner["owner_id"]
        seasons = query(conn, """
            select s.season_year, s.team_name, s.wins, s.losses, s.ties,
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
            select * from owner_head_to_head where owner_id = %s
            order by wins - losses desc, opponent_username
        """, (oid,))
        best = query(conn, """
            select season_year, week, points_for, opponent_username, result
            from game_log where owner_id = %s order by points_for desc limit 3
        """, (oid,))
        worst = query(conn, """
            select season_year, week, points_for, opponent_username, result
            from game_log where owner_id = %s order by points_for asc limit 3
        """, (oid,))
        proj_rows = query(conn, """
            select * from owner_projection_stats where owner_id = %s
        """, (oid,))
    return templates.TemplateResponse(
        request=request, name="team.html",
        context={"owner": owner, "seasons": seasons, "h2h": h2h,
                 "best": best, "worst": worst,
                 "projection": proj_rows[0] if proj_rows else None})


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
        request=request, name="seasons.html", context={"seasons": rows})


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
            select * from team_season_stats where season_year = %s
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
                 "weeks": weeks, "records": records})


@app.get("/rules", response_class=HTMLResponse)
def rules(request: Request):
    return templates.TemplateResponse(request=request, name="rules.html")


def keeper_context(conn, season, owner_id):
    windows = query(conn, """
        select phase, opens_at, closes_at, resolved_at,
               (resolved_at is null and now() between opens_at and closes_at) as is_open
        from keeper_windows where season_year = %s order by phase
    """, (season,))
    elig = query(conn, """
        select * from keeper_eligibility
        where for_season = %s and owner_id = %s
        order by cost_round nulls last, full_name
    """, (season, owner_id))
    contract_phase = {p["phase"]: p for p in query(conn, """
        select phase, player_id, contract_id, cost_round, years_remaining
        from keeper_phase_plan where season_year = %s and owner_id = %s
    """, (season, owner_id))}
    plans = {p["phase"]: p for p in query(conn, """
        select phase, player_id, term_years from keeper_plans
        where season_year = %s and owner_id = %s
    """, (season, owner_id))}
    subs = {s["phase"]: s for s in query(conn, """
        select s.phase, s.player_id, s.cost_round, s.term_years, s.origin,
               s.status, p.full_name
        from keeper_submissions s
        left join players p on p.player_id = s.player_id
        where s.season_year = %s and s.owner_id = %s
    """, (season, owner_id))}
    void_rows = query(conn, """
        select v.contract_id, v.confirmed_at, v.penalty_round, p.full_name
        from keeper_voids v
        join keeper_contracts c on c.contract_id = v.contract_id
        join players p on p.player_id = c.player_id
        where v.season_year = %s and v.owner_id = %s
    """, (season, owner_id))
    voids = {v["contract_id"] for v in void_rows}
    confirmed_voids = {v["contract_id"] for v in void_rows if v["confirmed_at"]}
    unconfirmed_voids = [v for v in void_rows if not v["confirmed_at"]]

    by_id = {e["player_id"]: e for e in elig}
    phases = []
    for w in windows:
        n = w["phase"]
        c = contract_phase.get(n)
        p = plans.get(n)
        phases.append({
            "n": n, "opens_at": w["opens_at"], "closes_at": w["closes_at"],
            "resolved_at": w["resolved_at"], "is_open": w["is_open"],
            "contract": by_id.get(c["player_id"]) if c else None,
            "submission": subs.get(n),
            "planned_id": p["player_id"] if p else None,
            "planned_term": p["term_years"] if p else None,
        })

    has_plans = any(p["player_id"] for p in plans.values())
    choices = [e for e in elig if e["state"] in ("free", "must_sign")]
    return {
        "windows": windows, "phases": phases,
        "contracts": [e for e in elig if e["state"] == "contract"],
        "choices": choices,
        "blocked": [e for e in elig if e["state"].startswith("ineligible")],
        "voids": voids,
        "has_plans": has_plans,
        "confirmed_voids": confirmed_voids,
        "unconfirmed_voids": unconfirmed_voids,
        "payload": [{
            "id": e["player_id"], "name": e["full_name"], "pos": e["position"],
            "state": e["state"], "cost": e["cost_round"],
            "later": e["contract_price_later"], "basis": e["basis_source"],
        } for e in choices],
    }


# ---------------------------------------------------------------------------
# REPLACE the existing keepers() function in app/main.py with this whole block.
#
# Find this line in app/main.py:
#
#     @app.get("/keepers", response_class=HTMLResponse)
#
# Select from there down to (but NOT including) the line:
#
#     @app.post("/keepers/plan")
#
# ...and paste this in its place.
# ---------------------------------------------------------------------------


@app.get("/keepers", response_class=HTMLResponse)
def keepers(request: Request, season: int = 0, owner: int = 0,
            error: str = "", submitted: int = 0, msg: str = ""):
    me = request.session.get("owner_id")
    is_admin = bool(request.session.get("is_admin"))

    with get_db() as conn:
        years = query(conn, """
            select distinct season_year from keeper_windows order by season_year desc
        """)
        if not years:
            return templates.TemplateResponse(
                request=request, name="keepers.html",
                context={"years": [], "season": 0, "owners": [], "owner": 0,
                         "who": None, "is_admin": is_admin, "editable": False,
                         "ctx": None, "error": error, "submitted": submitted, "msg": msg})

        if not season:
            season = years[0]["season_year"]

        owners = query(conn, """
            select distinct o.owner_id, o.username
            from keeper_eligibility k join owners o on o.owner_id = k.owner_id
            where k.for_season = %s order by o.username
        """, (season,))

        target = owner if (is_admin and owner) else me
        who = next((o for o in owners if o["owner_id"] == target), None)
        ctx = keeper_context(conn, season, target) if who else None

    return templates.TemplateResponse(
        request=request, name="keepers.html",
        context={"years": years, "season": season, "owners": owners,
                 "owner": target, "who": who, "is_admin": is_admin,
                 "editable": is_admin or target == me, "ctx": ctx,
                 "error": error, "submitted": submitted, "msg": msg})


@app.post("/keepers/plan")
async def save_plan(request: Request):
    me = request.session.get("owner_id")
    is_admin = bool(request.session.get("is_admin"))
    form = await request.form()
    season = int(form["season"])
    target = int(form.get("owner") or me)
    if target != me and not is_admin:
        raise HTTPException(status_code=403, detail="Not your selection")

    from urllib.parse import quote
    with get_db() as conn:
        with conn.cursor() as cur:
            problems = _plan_conflicts(cur, season, target, form)
        if problems:
            return RedirectResponse(
                url=f"/keepers?season={season}&owner={target}&error=" +
                    quote(" ".join(problems)), status_code=303)

        with conn.cursor() as cur:
            cur.execute("""
                select phase, resolved_at from keeper_windows where season_year = %s
            """, (season,))
            resolved = {r["phase"] for r in cur.fetchall() if r["resolved_at"]}

            for n in (1, 2, 3):
                if n in resolved:
                    continue
                raw = (form.get(f"p{n}_player") or "").strip()
                term = (form.get(f"p{n}_term") or "").strip()
                cur.execute("""
                    delete from keeper_plans
                    where season_year = %s and phase = %s and owner_id = %s
                """, (season, n, target))
                if raw:
                    cur.execute("""
                        insert into keeper_plans
                            (season_year, phase, owner_id, player_id,
                             term_years, updated_by)
                        values (%s, %s, %s, %s, %s, %s)
                    """, (season, n, target, int(raw),
                          int(term) if term else None, me))

            if 1 not in resolved:
                cur.execute("""
                    delete from keeper_voids
                    where season_year = %s and owner_id = %s and confirmed_at is null
                """, (season, target))
                for cid in form.getlist("void"):
                    cur.execute("""
                        insert into keeper_voids
                            (season_year, contract_id, owner_id, penalty_round)
                        select %s, k.contract_id, k.owner_id, k.void_penalty_round
                        from keeper_eligibility k
                        where k.for_season = %s and k.contract_id = %s
                          and k.owner_id = %s
                        on conflict do nothing
                    """, (season, season, int(cid), target))
        conn.commit()

    return RedirectResponse(
        url=f"/keepers?season={season}&owner={target}&msg=" + quote("Plan saved"), status_code=303)


LAYOUTS = {
    "regular":      ["regular"] * 6,
    "quarterfinal": ["quarterfinal"] * 4 + ["consolation"] * 4,
    "semifinal":    ["semifinal"] * 2 + ["fifth_place"] + ["consolation"] * 2 + ["eleventh_place"],
    "final":        ["championship", "third_place", "seventh_place", "ninth_place"],
}

MODE_LABELS = {
    "regular": "Regular season", "quarterfinal": "Quarterfinals",
    "semifinal": "Semifinals", "final": "Final",
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
        teams_list = query(conn, """
            select t.team_id, t.team_name, o.username
            from teams t join owners o on o.owner_id = t.owner_id
            where t.season_year = %s order by t.team_name
        """, (season,))
        existing = []
        if week:
            existing = query(conn, """
                select week, game_type, team_a_id, team_b_id,
                       team_a_points, team_b_points,
                       team_a_projected, team_b_projected
                from matchups where season_year = %s and week = %s
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
                 "teams": teams_list, "rows": build_rows(mode, existing),
                 "filled": filled, "saved": saved, "errors": [],
                 "labels": MODE_LABELS})


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
        teams_list = query(conn, """
            select t.team_id, t.team_name, o.username
            from teams t join owners o on o.owner_id = t.owner_id
            where t.season_year = %s order by t.team_name
        """, (season,))
        filled = query(conn, """
            select week from matchups where season_year = %s
            group by week order by week
        """, (season,))

    names = {t["team_id"]: t["team_name"] for t in teams_list}
    errors = []
    for t in sorted({x for x in seen if seen.count(x) > 1}):
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
                     "teams": teams_list, "rows": rows, "filled": filled,
                     "saved": 0, "errors": errors, "labels": MODE_LABELS})

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


@app.get("/health")
def health():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1")
            cur.fetchone()
    return {"status": "ok", "database": "connected"}


def _first_free(want, taken):
    for r in range(want, 0, -1):
        if r not in taken:
            return r
    return None


def resolve_keeper_phase(conn, season, phase, apply=False):
    """Work out what closing a phase would do. Applies it when apply=True."""
    with conn.cursor() as cur:
        cur.execute("""
            select resolved_at from keeper_windows
            where season_year = %s and phase = %s
        """, (season, phase))
        win = cur.fetchone()
        if not win:
            return [], f"No window defined for {season} phase {phase}."
        if win["resolved_at"]:
            return [], f"Keeper {phase} was already resolved."
        if phase > 1:
            cur.execute("""
                select resolved_at from keeper_windows
                where season_year = %s and phase = %s
            """, (season, phase - 1))
            prev = cur.fetchone()
            if not prev or not prev["resolved_at"]:
                return [], f"Keeper {phase - 1} has not been resolved yet."

        cur.execute("""
            select distinct owner_id from keeper_eligibility where for_season = %s
        """, (season,))
        owners = [r["owner_id"] for r in cur.fetchall()]

        cur.execute("""
            select owner_id, phase, player_id, cost_round, origin, status
            from keeper_submissions where season_year = %s
        """, (season,))
        subs, taken = {}, {}
        for r in cur.fetchall():
            subs[(r["owner_id"], r["phase"])] = r
            if r["cost_round"] and r["phase"] < phase:
                taken.setdefault(r["owner_id"], set()).add(r["cost_round"])

        cur.execute("""
            select owner_id, penalty_round from keeper_voids
            where season_year = %s and confirmed_at is not null
        """, (season,))
        for r in cur.fetchall():
            taken.setdefault(r["owner_id"], set()).add(r["penalty_round"])

        cur.execute("""
            select owner_id, phase, player_id, contract_id, cost_round
            from keeper_phase_plan where season_year = %s
        """, (season,))
        contracts = {(r["owner_id"], r["phase"]): r for r in cur.fetchall()}

        cur.execute("""
            select owner_id, player_id, term_years from keeper_plans
            where season_year = %s and phase = %s
        """, (season, phase))
        plans = {r["owner_id"]: r for r in cur.fetchall()}

        cur.execute("""
            select owner_id, player_id, cost_round, full_name
            from keeper_eligibility
            where for_season = %s and state in ('free', 'must_sign')
        """, (season,))
        costs = {(r["owner_id"], r["player_id"]): r for r in cur.fetchall()}

        cur.execute("select owner_id, username from owners")
        names = {r["owner_id"]: r["username"] for r in cur.fetchall()}
        cur.execute("select player_id, full_name from players")
        pnames = {r["player_id"]: r["full_name"] for r in cur.fetchall()}

    actions = []
    for oid in sorted(owners, key=lambda o: names.get(o, "")):
        if (oid, phase) in subs:
            s = subs[(oid, phase)]
            actions.append({"owner": names.get(oid), "owner_id": oid,
                            "kind": "leave", "player": pnames.get(s["player_id"], ""),
                            "round": s["cost_round"],
                            "note": f"already submitted ({s['origin']}, {s['status']})"})
            continue

        c = contracts.get((oid, phase))
        if c:
            rd = _first_free(c["cost_round"], taken.get(oid, set()))
            if rd is None:
                actions.append({"owner": names.get(oid), "owner_id": oid,
                                "kind": "error", "player": pnames.get(c["player_id"], ""),
                                "round": None,
                                "note": f"contract wants R{c['cost_round']}, no free round"})
                continue
            note = "contract"
            if rd != c["cost_round"]:
                note += f", moved from R{c['cost_round']}"
            actions.append({"owner": names.get(oid), "owner_id": oid, "kind": "auto",
                            "player": pnames.get(c["player_id"], ""), "round": rd,
                            "note": note, "player_id": c["player_id"],
                            "contract_id": c["contract_id"]})
            continue

        p = plans.get(oid)
        if p and p["player_id"]:
            e = costs.get((oid, p["player_id"]))
            if not e:
                actions.append({"owner": names.get(oid), "owner_id": oid,
                                "kind": "error",
                                "player": pnames.get(p["player_id"], ""),
                                "round": None, "note": "planned player not eligible"})
                continue
            rd = _first_free(e["cost_round"], taken.get(oid, set()))
            if rd is None:
                actions.append({"owner": names.get(oid), "owner_id": oid,
                                "kind": "error", "player": e["full_name"],
                                "round": None,
                                "note": f"wants R{e['cost_round']}, no free round"})
                continue
            note = "from plan"
            if rd != e["cost_round"]:
                note += f", moved from R{e['cost_round']}"
            actions.append({"owner": names.get(oid), "owner_id": oid, "kind": "plan",
                            "player": e["full_name"], "round": rd, "note": note,
                            "player_id": p["player_id"],
                            "term_years": p["term_years"]})
            continue

        actions.append({"owner": names.get(oid), "owner_id": oid, "kind": "forfeit",
                        "player": "", "round": None,
                        "note": "no contract, no plan, nothing submitted"})

    if any(a["kind"] == "error" for a in actions):
        return actions, "Errors above. Nothing was written."

    if not apply:
        return actions, None

    with conn.cursor() as cur:
        for a in actions:
            if a["kind"] == "auto":
                cur.execute("""
                    insert into keeper_submissions
                        (season_year, phase, owner_id, player_id, cost_round,
                         contract_id, origin, status, note)
                    values (%s, %s, %s, %s, %s, %s, 'auto', 'approved', %s)
                """, (season, phase, a["owner_id"], a["player_id"], a["round"],
                      a["contract_id"], a["note"]))
            elif a["kind"] == "plan":
                cur.execute("""
                    insert into keeper_submissions
                        (season_year, phase, owner_id, player_id, cost_round,
                         term_years, origin, status, note)
                    values (%s, %s, %s, %s, %s, %s, 'plan', 'pending', %s)
                """, (season, phase, a["owner_id"], a["player_id"], a["round"],
                      a["term_years"], a["note"]))
            elif a["kind"] == "forfeit":
                cur.execute("""
                    insert into keeper_submissions
                        (season_year, phase, owner_id, origin, status, note)
                    values (%s, %s, %s, 'forfeit', 'approved', %s)
                """, (season, phase, a["owner_id"], a["note"]))

        if phase == 1:
            cur.execute("""
                delete from keeper_voids
                where season_year = %s and confirmed_at is null
            """, (season,))

        cur.execute("""
            update keeper_windows set resolved_at = now(), updated_at = now()
            where season_year = %s and phase = %s
        """, (season, phase))
    conn.commit()
    return actions, None


@app.get("/admin/keepers", response_class=HTMLResponse)
def admin_keepers(request: Request, season: int = 0, preview: int = 0,
                  msg: str = "", error: str = ""):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")

    with get_db() as conn:
        years = query(conn, "select season_year from seasons order by season_year desc")
        if not season:
            season = years[0]["season_year"]

        windows = query(conn, """
            select phase, opens_at, closes_at, resolved_at,
                   (resolved_at is null and now() between opens_at and closes_at) as is_open
            from keeper_windows where season_year = %s order by phase
        """, (season,))

        all_owners = query(conn, """
            select distinct o.owner_id, o.username
            from keeper_eligibility k join owners o on o.owner_id = k.owner_id
            where k.for_season = %s order by o.username
        """, (season,))

        pending = query(conn, """
            select s.submission_id, s.phase, s.cost_round, s.term_years,
                   s.origin, s.note, s.submitted_at, o.username, p.full_name
            from keeper_submissions s
            join owners o on o.owner_id = s.owner_id
            left join players p on p.player_id = s.player_id
            where s.season_year = %s and s.status = 'pending'
            order by s.phase, o.username
        """, (season,))

        settled = query(conn, """
            select s.phase, s.cost_round, s.origin, s.status,
                   o.username, p.full_name
            from keeper_submissions s
            join owners o on o.owner_id = s.owner_id
            left join players p on p.player_id = s.player_id
            where s.season_year = %s and s.status <> 'pending'
            order by s.phase, o.username
        """, (season,))

        over = query(conn, """
            select o.username, count(*) as n
            from keeper_eligibility k join owners o on o.owner_id = k.owner_id
            where k.for_season = %s and k.state = 'contract'
            group by o.username having count(*) > 3 order by 2 desc
        """, (season,))

        voids = query(conn, """
            select o.username, p.full_name, v.penalty_round, v.confirmed_at,
                   v.contract_id, v.owner_id
            from keeper_voids v
            join owners o on o.owner_id = v.owner_id
            join keeper_contracts c on c.contract_id = v.contract_id
            join players p on p.player_id = c.player_id
            where v.season_year = %s order by o.username
        """, (season,))

        voidable = query(conn, """
            select k.contract_id, k.owner_id, o.username, k.full_name,
                   k.cost_round, k.void_penalty_round
            from keeper_eligibility k
            join owners o on o.owner_id = k.owner_id
            where k.for_season = %s and k.state = 'contract'
              and k.contract_id not in (
                  select contract_id from keeper_voids where season_year = %s)
            order by o.username, k.cost_round
        """, (season, season))

        actions = []
        if preview:
            actions, err = resolve_keeper_phase(conn, season, preview, apply=False)
            if err and not error:
                error = err

    return templates.TemplateResponse(
        request=request, name="admin_keepers.html",
        context={"years": years, "season": season, "windows": windows,
                 "pending": pending, "settled": settled, "over": over,
                 "voids": voids, "voidable": voidable,
                 "preview": preview, "actions": actions,
                 "all_owners": all_owners,
                 "msg": msg, "error": error})


@app.post("/admin/keepers/windows")
async def admin_keeper_windows(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    form = await request.form()
    season = int(form["season"])

    with get_db() as conn:
        with conn.cursor() as cur:
            for n in (1, 2, 3):
                opens = (form.get(f"w{n}_opens") or "").strip()
                closes = (form.get(f"w{n}_closes") or "").strip()
                if not opens or not closes:
                    continue
                cur.execute("""
                    insert into keeper_windows (season_year, phase, opens_at, closes_at)
                    values (%s, %s, %s, %s)
                    on conflict (season_year, phase) do update set
                        opens_at = excluded.opens_at,
                        closes_at = excluded.closes_at,
                        updated_at = now()
                """, (season, n, opens, closes))
        conn.commit()

    return RedirectResponse(
        url=f"/admin/keepers?season={season}&msg=Windows+saved", status_code=303)


@app.post("/admin/keepers/resolve")
async def admin_keeper_resolve(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    phase = int(form["phase"])

    with get_db() as conn:
        actions, err = resolve_keeper_phase(conn, season, phase, apply=True)

    if err:
        return RedirectResponse(
            url=f"/admin/keepers?season={season}&error={quote(err)}", status_code=303)
    written = sum(1 for a in actions if a["kind"] != "leave")
    return RedirectResponse(
        url=f"/admin/keepers?season={season}&msg=" +
            quote(f"Keeper {phase} resolved, {written} rows written"),
        status_code=303)


@app.post("/admin/keepers/review")
async def admin_keeper_review(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    me = request.session.get("owner_id")
    form = await request.form()
    season = int(form["season"])
    approve = form.get("action") == "approve"

    with get_db() as conn:
        with conn.cursor() as cur:
            for sid in form.getlist("submission"):
                if approve:
                    cur.execute("""
                        update keeper_submissions
                        set status = 'approved', reviewed_by = %s, reviewed_at = now()
                        where submission_id = %s and season_year = %s
                    """, (me, int(sid), season))
                else:
                    cur.execute("""
                        delete from keeper_submissions
                        where submission_id = %s and season_year = %s
                    """, (int(sid), season))
        conn.commit()

    n = len(form.getlist("submission"))
    word = "approved" if approve else "rejected and reopened"
    return RedirectResponse(
        url=f"/admin/keepers?season={season}&msg=" + quote(f"{n} {word}"),
        status_code=303)


@app.post("/admin/keepers/edit")
async def admin_keeper_edit(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    me = request.session.get("owner_id")
    form = await request.form()
    season = int(form["season"])
    sid = int(form["submission_id"])
    action = form.get("action")

    with get_db() as conn:
        with conn.cursor() as cur:
            if action == "delete":
                cur.execute("""
                    delete from keeper_submissions
                    where submission_id = %s and season_year = %s
                """, (sid, season))
                conn.commit()
                return RedirectResponse(
                    url=f"/admin/keepers?season={season}&msg=" +
                        quote("Submission removed, that phase is open again"),
                    status_code=303)

            raw = (form.get("player_id") or "").strip()
            rd = (form.get("cost_round") or "").strip()
            term = (form.get("term_years") or "").strip()
            status = form.get("status") or "approved"
            note = (form.get("note") or "").strip() or "edited by admin"

            if not raw:
                cur.execute("""
                    update keeper_submissions
                    set player_id = null, cost_round = null, term_years = null,
                        contract_id = null, origin = 'forfeit', status = 'approved',
                        note = %s, reviewed_by = %s, reviewed_at = now()
                    where submission_id = %s and season_year = %s
                """, (note, me, sid, season))
            else:
                if not rd:
                    conn.rollback()
                    return RedirectResponse(
                        url=f"/admin/keepers?season={season}&error=" +
                            quote("A round is required when a player is set"),
                        status_code=303)
                cur.execute("""
                    update keeper_submissions
                    set player_id = %s, cost_round = %s, term_years = %s,
                        origin = case when origin = 'forfeit' then 'manual' else origin end,
                        status = %s, note = %s,
                        reviewed_by = %s, reviewed_at = now()
                    where submission_id = %s and season_year = %s
                """, (int(raw), int(rd), int(term) if term else None,
                      status, note, me, sid, season))
        conn.commit()

    return RedirectResponse(
        url=f"/admin/keepers?season={season}&msg=" + quote("Submission updated"),
        status_code=303)


@app.get("/admin/keepers/edit/{sid}", response_class=HTMLResponse)
def admin_keeper_edit_form(request: Request, sid: int):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")

    with get_db() as conn:
        rows = query(conn, """
            select s.*, o.username, p.full_name
            from keeper_submissions s
            join owners o on o.owner_id = s.owner_id
            left join players p on p.player_id = s.player_id
            where s.submission_id = %s
        """, (sid,))
        if not rows:
            raise HTTPException(status_code=404, detail="No such submission")
        sub = rows[0]

        options = query(conn, """
            select player_id, full_name, position, state, cost_round
            from keeper_eligibility
            where for_season = %s and owner_id = %s
            order by state, cost_round nulls last, full_name
        """, (sub["season_year"], sub["owner_id"]))

        used = query(conn, """
            select phase, cost_round, player_id from keeper_submissions
            where season_year = %s and owner_id = %s and submission_id <> %s
            order by phase
        """, (sub["season_year"], sub["owner_id"], sid))

    return templates.TemplateResponse(
        request=request, name="admin_keeper_edit.html",
        context={"sub": sub, "options": options, "used": used})


def _save_plans(cur, season, target, me, form):
    cur.execute("""
        select phase, resolved_at from keeper_windows where season_year = %s
    """, (season,))
    resolved = {r["phase"] for r in cur.fetchall() if r["resolved_at"]}

    cur.execute("""
        select phase from keeper_submissions
        where season_year = %s and owner_id = %s
    """, (season, target))
    submitted = {r["phase"] for r in cur.fetchall()}

    for n in (1, 2, 3):
        if n in resolved or n in submitted:
            continue
        raw = (form.get(f"p{n}_player") or "").strip()
        term = (form.get(f"p{n}_term") or "").strip()
        cur.execute("""
            delete from keeper_plans
            where season_year = %s and phase = %s and owner_id = %s
        """, (season, n, target))
        if raw:
            cur.execute("""
                insert into keeper_plans
                    (season_year, phase, owner_id, player_id, term_years, updated_by)
                values (%s, %s, %s, %s, %s, %s)
            """, (season, n, target, int(raw), int(term) if term else None, me))

    if 1 not in resolved and 1 not in submitted:
        cur.execute("""
            delete from keeper_voids
            where season_year = %s and owner_id = %s and confirmed_at is null
        """, (season, target))
        for cid in form.getlist("void"):
            cur.execute("""
                insert into keeper_voids
                    (season_year, contract_id, owner_id, penalty_round)
                select %s, k.contract_id, k.owner_id, k.void_penalty_round
                from keeper_eligibility k
                where k.for_season = %s and k.contract_id = %s and k.owner_id = %s
                on conflict do nothing
            """, (season, season, int(cid), target))


def _rounds_taken(cur, season, target, before_phase):
    taken = set()
    cur.execute("""
        select phase, cost_round from keeper_submissions
        where season_year = %s and owner_id = %s and cost_round is not null
    """, (season, target))
    for r in cur.fetchall():
        if r["phase"] < before_phase:
            taken.add(r["cost_round"])
    cur.execute("""
        select phase, cost_round from keeper_phase_plan
        where season_year = %s and owner_id = %s
    """, (season, target))
    for r in cur.fetchall():
        if r["phase"] < before_phase:
            taken.add(r["cost_round"])
    cur.execute("""
        select penalty_round from keeper_voids
        where season_year = %s and owner_id = %s
    """, (season, target))
    for r in cur.fetchall():
        taken.add(r["penalty_round"])
    return taken


@app.post("/keepers/submit")
async def submit_phase(request: Request):
    from urllib.parse import quote
    me = request.session.get("owner_id")
    is_admin = bool(request.session.get("is_admin"))
    form = await request.form()
    season = int(form["season"])
    target = int(form.get("owner") or me)
    phase = int(form["phase"])

    if target != me and not is_admin:
        raise HTTPException(status_code=403, detail="Not your selection")

    err = None
    with get_db() as conn:
        with conn.cursor() as cur:
            _save_plans(cur, season, target, me, form)

            cur.execute("""
                select resolved_at, (now() between opens_at and closes_at) as is_open
                from keeper_windows where season_year = %s and phase = %s
            """, (season, phase))
            win = cur.fetchone()
            if not win:
                err = "No such phase."
            elif win["resolved_at"]:
                err = f"Keeper {phase} has already closed."
            elif not win["is_open"] and not is_admin:
                err = f"The Keeper {phase} window is not open."

            if not err:
                cur.execute("""
                    select 1 from keeper_submissions
                    where season_year = %s and phase = %s and owner_id = %s
                """, (season, phase, target))
                if cur.fetchone():
                    err = f"Keeper {phase} is already submitted."

            if not err and phase == 1:
                cur.execute("""
                    select count(*) as n from keeper_voids
                    where season_year = %s and owner_id = %s and confirmed_at is null
                """, (season, target))
                if cur.fetchone()["n"]:
                    err = ("Submit or clear your void first. Voiding changes which "
                           "rounds are available.")

            if not err:
                cur.execute("""
                    select player_id from keeper_phase_plan
                    where season_year = %s and owner_id = %s and phase = %s
                """, (season, target, phase))
                if cur.fetchone():
                    err = (f"Keeper {phase} is filled by a contract. "
                           "It submits itself when the window closes.")

            planned = None
            elig = None
            if not err:
                cur.execute("""
                    select player_id, term_years from keeper_plans
                    where season_year = %s and owner_id = %s and phase = %s
                """, (season, target, phase))
                planned = cur.fetchone()
                if not planned or not planned["player_id"]:
                    err = f"Choose a player for Keeper {phase} first."

            if not err:
                cur.execute("""
                    select full_name, state, cost_round from keeper_eligibility
                    where for_season = %s and owner_id = %s and player_id = %s
                """, (season, target, planned["player_id"]))
                elig = cur.fetchone()
                if not elig or elig["state"] not in ("free", "must_sign"):
                    err = "That player is not eligible."
                elif elig["state"] == "must_sign" and not planned["term_years"]:
                    err = f"Choose a contract term for {elig['full_name']}."

            if not err:
                taken = _rounds_taken(cur, season, target, phase)
                want = elig["cost_round"]
                rd = next((r for r in range(want, 0, -1) if r not in taken), None)
                if rd is None:
                    err = (f"{elig['full_name']} cannot be kept, "
                           f"no free round at or below R{want}.")
                else:
                    note = "submitted manually"
                    if rd != want:
                        note += f", moved from R{want} which is taken"
                    cur.execute("""
                        insert into keeper_submissions
                            (season_year, phase, owner_id, player_id, cost_round,
                             term_years, origin, status, note)
                        values (%s, %s, %s, %s, %s, %s, 'manual', 'pending', %s)
                    """, (season, phase, target, planned["player_id"], rd,
                          planned["term_years"], note))
        conn.commit()

    url = f"/keepers?season={season}&owner={target}"
    url += ("&error=" + quote(err) if err
            else "&msg=" + quote(f"Keeper {phase} submitted and locked"))
    return RedirectResponse(url=url, status_code=303)



@app.post("/admin/keepers/reset")
async def admin_keeper_reset(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    scope = form.get("scope")
    owner_id = form.get("owner_id")

    if not form.get("confirm"):
        return RedirectResponse(
            url=f"/admin/keepers?season={season}&error=" +
                quote("Tick the confirm box first"), status_code=303)

    with get_db() as conn:
        with conn.cursor() as cur:
            if scope == "owner" and owner_id:
                oid = int(owner_id)
                cur.execute("""
                    delete from keeper_submissions
                    where season_year = %s and owner_id = %s
                """, (season, oid))
                cur.execute("""
                    delete from keeper_plans
                    where season_year = %s and owner_id = %s
                """, (season, oid))
                cur.execute("""
                    delete from keeper_voids
                    where season_year = %s and owner_id = %s
                """, (season, oid))
                cur.execute("select username from owners where owner_id = %s", (oid,))
                row = cur.fetchone()
                what = f"{row['username'] if row else oid} reset"
            else:
                cur.execute("delete from keeper_submissions where season_year = %s",
                            (season,))
                cur.execute("delete from keeper_plans where season_year = %s",
                            (season,))
                cur.execute("delete from keeper_voids where season_year = %s",
                            (season,))
                cur.execute("""
                    update keeper_windows set resolved_at = null, updated_at = now()
                    where season_year = %s
                """, (season,))
                what = f"all of {season} reset, phases reopened"
        conn.commit()

    return RedirectResponse(
        url=f"/admin/keepers?season={season}&msg=" + quote(what), status_code=303)



@app.get("/draft-order", response_class=HTMLResponse)
def draft_order(request: Request, season: int = 0, msg: str = "", error: str = ""):
    me = request.session.get("owner_id")
    is_admin = bool(request.session.get("is_admin"))

    with get_db() as conn:
        years = query(conn, "select season_year from seasons order by season_year desc")
        if not season:
            season = years[0]["season_year"]

        rows = query(conn, """
            select * from draft_order_state
            where season_year = %s order by lottery_position
        """, (season,))

        size = query(conn, "select team_count from seasons where season_year = %s",
                     (season,))
        team_count = size[0]["team_count"] if size else 12

    taken = {r["slot"]: r["username"] for r in rows if r["slot"]}
    on_clock = next((r for r in rows if r["is_on_the_clock"]), None)
    my_turn = bool(on_clock and on_clock["owner_id"] == me)

    return templates.TemplateResponse(
        request=request, name="draft_order.html",
        context={"years": years, "season": season, "rows": rows,
                 "slots": list(range(1, team_count + 1)), "taken": taken,
                 "on_clock": on_clock, "my_turn": my_turn,
                 "is_admin": is_admin, "me": me, "msg": msg, "error": error})


@app.post("/draft-order/pick")
async def draft_order_pick(request: Request):
    from urllib.parse import quote
    me = request.session.get("owner_id")
    is_admin = bool(request.session.get("is_admin"))
    form = await request.form()
    season = int(form["season"])
    slot = int(form["slot"])
    target = int(form.get("owner_id") or me)

    err = None
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select owner_id, slot, is_on_the_clock from draft_order_state
                where season_year = %s and owner_id = %s
            """, (season, target))
            row = cur.fetchone()
            if not row:
                err = "That manager is not in the draft order."
            elif row["slot"]:
                err = "They have already chosen."
            elif not row["is_on_the_clock"] and not is_admin:
                err = "It is not their turn."
            elif target != me and not is_admin:
                err = "You can only pick for yourself."

            if not err:
                cur.execute("""
                    select username from draft_order_state
                    where season_year = %s and slot = %s
                """, (season, slot))
                clash = cur.fetchone()
                if clash:
                    err = f"Slot {slot} is already taken by {clash['username']}."

            if not err:
                cur.execute("""
                    update draft_order
                    set slot = %s, chosen_at = now(), chosen_by = %s
                    where season_year = %s and owner_id = %s
                """, (slot, me, season, target))
        conn.commit()

    url = f"/draft-order?season={season}"
    url += "&error=" + quote(err) if err else "&msg=" + quote(f"Slot {slot} taken")
    return RedirectResponse(url=url, status_code=303)


@app.post("/admin/draft-order/lottery")
async def admin_draft_lottery(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])

    if not form.get("confirm"):
        return RedirectResponse(
            url=f"/draft-order?season={season}&error=" +
                quote("Tick the confirm box first"), status_code=303)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from draft_order where season_year = %s", (season,))
            cur.execute("""
                insert into draft_order (season_year, owner_id, lottery_position)
                select %s, t.owner_id,
                       row_number() over (order by random())
                from teams t
                where t.season_year = %s
            """, (season, season))
            cur.execute("select count(*) as n from draft_order where season_year = %s",
                        (season,))
            n = cur.fetchone()["n"]
        conn.commit()

    return RedirectResponse(
        url=f"/draft-order?season={season}&msg=" +
            quote(f"Lottery drawn for {n} managers"), status_code=303)


@app.post("/admin/draft-order/set")
async def admin_draft_set(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    me = request.session.get("owner_id")
    form = await request.form()
    season = int(form["season"])

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                update draft_order set slot = null, chosen_at = null, chosen_by = null
                where season_year = %s
            """, (season,))
            for key in form.keys():
                if not key.startswith("slot_"):
                    continue
                raw = (form.get(key) or "").strip()
                if not raw:
                    continue
                oid = int(key.split("_", 1)[1])
                cur.execute("""
                    update draft_order
                    set slot = %s, chosen_at = now(), chosen_by = %s
                    where season_year = %s and owner_id = %s
                """, (int(raw), me, season, oid))
        conn.commit()

    return RedirectResponse(
        url=f"/draft-order?season={season}&msg=" + quote("Slots updated"),
        status_code=303)



@app.get("/draft-prep", response_class=HTMLResponse)
def draft_prep(request: Request, season: int = 0, msg: str = "", error: str = ""):
    is_admin = bool(request.session.get("is_admin"))

    with get_db() as conn:
        years = query(conn, "select season_year from seasons order by season_year desc")
        if not season:
            season = years[0]["season_year"]

        order = query(conn, """
            select d.owner_id, o.username, d.slot
            from draft_order d join owners o on o.owner_id = d.owner_id
            where d.season_year = %s and d.slot is not null
            order by d.slot
        """, (season,))

        voided = {r["contract_id"] for r in query(conn, """
            select contract_id from keeper_voids where season_year = %s
        """, (season,))}

        elig = query(conn, """
            select k.owner_id, o.username, k.player_id, k.full_name, k.position,
                   k.state, k.cost_round, k.contract_id, k.final_season,
                   k.contract_price_later
            from keeper_eligibility k
            join owners o on o.owner_id = k.owner_id
            where k.for_season = %s and k.state in ('free', 'must_sign', 'contract')
            order by o.username, k.cost_round, k.full_name
        """, (season,))

        real = query(conn, """
            select s.owner_id, s.cost_round, s.term_years, s.player_id,
                   p.full_name, p.position
            from keeper_submissions s
            join players p on p.player_id = s.player_id
            where s.season_year = %s and s.status = 'approved'
              and s.player_id is not null
        """, (season,))

        holds = query(conn, """
            select h.owner_id, h.cost_round, h.player_id, p.full_name, p.position
            from keeper_placeholders h
            join players p on p.player_id = h.player_id
            where h.season_year = %s
        """, (season,))

        voids = query(conn, """
            select v.owner_id, v.penalty_round, p.full_name, v.confirmed_at
            from keeper_voids v
            join keeper_contracts c on c.contract_id = v.contract_id
            join players p on p.player_id = c.player_id
            where v.season_year = %s
        """, (season,))

    contracts = [e for e in elig
                 if e["state"] == "contract" and e["contract_id"] not in voided]

    cells = {}
    for c in contracts:
        cells[(c["owner_id"], c["cost_round"])] = {
            "name": c["full_name"], "kind": "real", "tag": "contract"}
    for r in real:
        cells[(r["owner_id"], r["cost_round"])] = {
            "name": r["full_name"], "kind": "real", "tag": "keeper"}
    for v in voids:
        cells.setdefault((v["owner_id"], v["penalty_round"]), {
            "name": "DEF (void)", "kind": "void", "tag": "void"})
    for h in holds:
        cells.setdefault((h["owner_id"], h["cost_round"]), {
            "name": h["full_name"], "kind": "hold", "tag": "placeholder"})

    board, n = [], 0
    for rnd in range(1, 14):
        cols = order if rnd % 2 == 1 else list(reversed(order))
        row = {}
        for o in cols:
            c = cells.get((o["owner_id"], rnd))
            if c:
                row[o["slot"]] = dict(c, pick=None)
            else:
                n += 1
                row[o["slot"]] = {"name": None, "kind": "open", "pick": n}
        board.append({"round": rnd, "cells": [row[o["slot"]] for o in order]})

    used = {r["player_id"] for r in real} | {h["player_id"] for h in holds}
    used |= {c["player_id"] for c in contracts}

    by_owner = []
    for o in order:
        oid = o["owner_id"]
        by_owner.append({
            "username": o["username"], "slot": o["slot"], "owner_id": oid,
            "contracts": [c for c in contracts if c["owner_id"] == oid],
            "voids": [v for v in voids if v["owner_id"] == oid],
            "keepers": sorted([r for r in real if r["owner_id"] == oid],
                              key=lambda x: x["cost_round"]),
            "holds": sorted([h for h in holds if h["owner_id"] == oid],
                            key=lambda x: x["cost_round"]),
            "options": [e for e in elig
                        if e["owner_id"] == oid
                        and e["state"] != "contract"
                        and e["player_id"] not in used],
            "all_options": [e for e in elig if e["owner_id"] == oid],
        })

    return templates.TemplateResponse(
        request=request, name="draft_prep.html",
        context={"years": years, "season": season, "order": order,
                 "board": board, "by_owner": by_owner,
                 "is_admin": is_admin, "msg": msg, "error": error})


@app.post("/draft-prep/placeholder")
async def draft_prep_placeholder(request: Request):
    from urllib.parse import quote
    me = request.session.get("owner_id")
    form = await request.form()
    season = int(form["season"])
    action = form.get("action")

    err = None
    with get_db() as conn:
        with conn.cursor() as cur:
            if action == "clear":
                cur.execute("delete from keeper_placeholders where season_year = %s",
                            (season,))
            elif action == "remove":
                cur.execute("""
                    delete from keeper_placeholders
                    where season_year = %s and owner_id = %s and player_id = %s
                """, (season, int(form["owner_id"]), int(form["player_id"])))
            else:
                oid = int(form["owner_id"])
                raw = (form.get("player_id") or "").strip()
                if not raw:
                    err = "Pick a player first."
                else:
                    pid = int(raw)
                    cur.execute("""
                        select cost_round, full_name from keeper_eligibility
                        where for_season = %s and owner_id = %s and player_id = %s
                    """, (season, oid, pid))
                    e = cur.fetchone()
                    if not e:
                        err = "That player is not eligible."
                    else:
                        cur.execute("""
                            select 1 from keeper_placeholders
                            where season_year = %s and owner_id = %s and cost_round = %s
                        """, (season, oid, e["cost_round"]))
                        if cur.fetchone():
                            err = (f"Already a placeholder at R{e['cost_round']} "
                                   "for that manager.")
                        else:
                            cur.execute("""
                                insert into keeper_placeholders
                                    (season_year, owner_id, player_id, cost_round,
                                     created_by)
                                values (%s, %s, %s, %s, %s)
                                on conflict do nothing
                            """, (season, oid, pid, e["cost_round"], me))
        conn.commit()

    url = f"/draft-prep?season={season}"
    if err:
        url += "&error=" + quote(err)
    return RedirectResponse(url=url, status_code=303)




@app.post("/keepers/void-submit")
async def submit_voids(request: Request):
    from urllib.parse import quote
    me = request.session.get("owner_id")
    is_admin = bool(request.session.get("is_admin"))
    form = await request.form()
    season = int(form["season"])
    target = int(form.get("owner") or me)

    if target != me and not is_admin:
        raise HTTPException(status_code=403, detail="Not your selection")

    err = None
    n = 0
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select resolved_at from keeper_windows
                where season_year = %s and phase = 1
            """, (season,))
            w = cur.fetchone()
            if w and w["resolved_at"]:
                err = "Keeper 1 has closed. Voids can no longer be submitted."
            else:
                wanted = [int(c) for c in form.getlist("void")]
                if not wanted:
                    err = "No contracts are marked for voiding."
                else:
                    cur.execute("""
                        delete from keeper_voids
                        where season_year = %s and owner_id = %s
                          and confirmed_at is null
                    """, (season, target))
                    for cid in wanted:
                        cur.execute("""
                            insert into keeper_voids
                                (season_year, contract_id, owner_id,
                                 penalty_round, confirmed_at)
                            select %s, k.contract_id, k.owner_id,
                                   k.void_penalty_round, now()
                            from keeper_eligibility k
                            where k.for_season = %s and k.contract_id = %s
                              and k.owner_id = %s
                            on conflict (season_year, contract_id) do update
                                set confirmed_at = now()
                        """, (season, season, cid, target))
                    cur.execute("""
                        delete from keeper_plans
                        where season_year = %s and owner_id = %s
                    """, (season, target))
                    cur.execute("""
                        select count(*) as n from keeper_voids
                        where season_year = %s and owner_id = %s
                          and confirmed_at is not null
                    """, (season, target))
                    n = cur.fetchone()["n"]
        conn.commit()

    url = f"/keepers?season={season}&owner={target}"
    if err:
        url += "&error=" + quote(err)
    else:
        url += "&msg=" + quote(f"{n} void(s) submitted and now binding")
    return RedirectResponse(url=url, status_code=303)







def _plan_conflicts(cur, season, target, form):
    """Return a list of problems with the plan as submitted."""
    taken = set()
    cur.execute("""
        select cost_round from keeper_submissions
        where season_year = %s and owner_id = %s and cost_round is not null
    """, (season, target))
    for r in cur.fetchall():
        taken.add(r["cost_round"])

    cur.execute("""
        select phase, cost_round from keeper_phase_plan
        where season_year = %s and owner_id = %s
    """, (season, target))
    contract_rounds = {r["phase"]: r["cost_round"] for r in cur.fetchall()}
    taken |= set(contract_rounds.values())

    cur.execute("""
        select penalty_round from keeper_voids
        where season_year = %s and owner_id = %s
    """, (season, target))
    for r in cur.fetchall():
        taken.add(r["penalty_round"])

    problems, seen = [], {}
    for n in (1, 2, 3):
        raw = (form.get(f"p{n}_player") or "").strip()
        if not raw:
            continue
        pid = int(raw)
        cur.execute("""
            select full_name, cost_round, state from keeper_eligibility
            where for_season = %s and owner_id = %s and player_id = %s
        """, (season, target, pid))
        e = cur.fetchone()
        if not e:
            problems.append(f"Keeper {n}: that player is not eligible.")
            continue
        if pid in seen:
            problems.append(
                f"{e['full_name']} is planned in Keeper {seen[pid]} and Keeper {n}.")
            continue
        seen[pid] = n

        rd = next((r for r in range(e["cost_round"], 0, -1) if r not in taken), None)
        if rd is None:
            problems.append(
                f"{e['full_name']} costs R{e['cost_round']} and every round "
                "at or below it is already used. Free one up first.")
            continue
        taken.add(rd)
    return problems





@app.post("/admin/keepers/void")
async def admin_keeper_void(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    action = form.get("action")

    with get_db() as conn:
        with conn.cursor() as cur:
            if action == "cancel":
                cid = int(form["contract_id"])
                cur.execute("""
                    delete from keeper_voids
                    where season_year = %s and contract_id = %s
                """, (season, cid))
                what = "Void cancelled. The contract stands again."
            elif action == "unconfirm":
                cid = int(form["contract_id"])
                cur.execute("""
                    update keeper_voids set confirmed_at = null
                    where season_year = %s and contract_id = %s
                """, (season, cid))
                what = "Void reopened. The manager can now change or resubmit it."
            else:
                cid = int(form["contract_id"])
                cur.execute("""
                    select owner_id from keeper_eligibility
                    where for_season = %s and contract_id = %s
                """, (season, cid))
                row = cur.fetchone()
                oid = row["owner_id"] if row else None
                if oid is None:
                    conn.rollback()
                    return RedirectResponse(
                        url=f"/admin/keepers?season={season}&error=" +
                            quote("That contract is not active this season"),
                        status_code=303)
                cur.execute("""
                    insert into keeper_voids
                        (season_year, contract_id, owner_id, penalty_round, confirmed_at)
                    select %s, k.contract_id, k.owner_id, k.void_penalty_round, now()
                    from keeper_eligibility k
                    where k.for_season = %s and k.contract_id = %s and k.owner_id = %s
                    on conflict (season_year, contract_id) do update
                        set confirmed_at = now()
                """, (season, season, cid, oid))
                cur.execute("""
                    delete from keeper_plans where season_year = %s and owner_id = %s
                """, (season, oid))
                what = "Void added and confirmed. Their plans were cleared."
        conn.commit()

    return RedirectResponse(
        url=f"/admin/keepers?season={season}&msg=" + quote(what), status_code=303)






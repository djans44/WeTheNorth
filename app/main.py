import os
import pathlib
import contextvars
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
PUBLIC_PATHS = {"/", "/login", "/logout", "/health", "/preview"}

# Sixteen swatches for thirteen owners. Every one is dark enough that
# parchment text sits on it legibly, which is why nothing in this codebase
# calculates contrast -- the avatar foreground is always #EDE4D3.
# Single source of truth: feeds the pickers and validates every save.
AVATAR_PALETTE = [
    ("Oxblood",      "#6B1F24"),
    ("Rust",         "#8C4425"),
    ("Antique gold", "#8A6D1F"),
    ("Olive",        "#55622F"),
    ("Forest",       "#2C4733"),
    ("Pine",         "#1F4A42"),
    ("Deep teal",    "#1B5560"),
    ("Northern ice", "#3C6E88"),
    ("Slate",        "#2F4A66"),
    ("Midnight",     "#1E2A44"),
    ("Royal purple", "#472B57"),
    ("Plum",         "#632C51"),
    ("Charcoal",     "#33383D"),
    ("Stone",        "#5E5D55"),
    ("Bone",         "#7A6A55"),
    ("Ash",          "#4A4E52"),
]
AVATAR_HEXES = {hex_ for _, hex_ in AVATAR_PALETTE}

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
def crest_when(row):
    """When a crest was won, and what earned it, on one line.

    Season, week and detail are each optional -- a career crest has no season,
    a season crest has no week, and some crests carry no number worth naming --
    so the parts are joined only where they exist rather than assembled into
    "None, week None".
    """
    parts = []
    if row.get("season_year"):
        parts.append(str(row["season_year"])
                     + (f" week {row['week']}" if row.get("week") else ""))
    if row.get("detail"):
        parts.append(row["detail"])
    return " · ".join(parts)


templates.env.globals["ordinal"] = ordinal
templates.env.globals["crest_when"] = crest_when


# --- records ----------------------------------------------------------
#
# One set of shapes for both pages. /history and /season/{year} ask the same
# questions of different slices, and when they each carried their own copy of
# the SQL the two drifted -- /history counting playoff games as league records
# while the season page had already stopped.
#
# {scope} is a literal written here, never anything a request supplies; the
# values still go through bound parameters.
RECORD_SQL = {
    "high": """
        select username, opponent_username, season_year, week, points_for
        from game_log where {scope} order by points_for desc limit {n}""",
    "low": """
        select username, opponent_username, season_year, week, points_for
        from game_log where {scope} order by points_for asc limit {n}""",
    "blowouts": """
        select username, opponent_username, season_year, week,
               points_for, points_against,
               points_for - points_against as margin
        from game_log where {scope} and result = 'W'
        order by margin desc limit {n}""",
    "nailbiters": """
        select username, opponent_username, season_year, week,
               points_for, points_against,
               points_for - points_against as margin
        from game_log where {scope} and result = 'W'
        order by margin asc limit {n}""",
    "shootouts": """
        select username, opponent_username, season_year, week,
               points_for + points_against as combined
        from game_log where {scope} and result = 'W'
        order by combined desc limit {n}""",
    # The losing half of nailbiters. Margin is written the way round that
    # makes it a positive number of points short.
    "heartbreaks": """
        select username, opponent_username, season_year, week,
               points_for, points_against,
               points_against - points_for as margin
        from game_log where {scope} and result = 'L'
        order by margin asc limit {n}""",
}

# Named in the same voice on both pages, so the same record is not called two
# things depending on where you read it.
RECORD_NAMES = {
    "high": "Mightiest weeks",
    "low": "Bleakest weeks",
    "blowouts": "Greatest routs",
    "nailbiters": "Narrowest escapes",
    "shootouts": "Bloodiest fields",
    "heartbreaks": "Cruellest defeats",
}

ALL_RECORDS = ("high", "low", "blowouts", "nailbiters", "shootouts")

# The championship side of the bracket, and nothing else. Consolation and the
# seventh, ninth and eleventh place games are played in the same weeks, but by
# teams already out of it; a record set there is not a playoff record.
# fifth_place belongs: it is the two beaten quarterfinalists, both qualifiers.
BRACKET_TYPES = ("quarterfinal", "semifinal", "championship",
                 "third_place", "fifth_place")
BRACKET_SCOPE = "game_type in (%s)" % ", ".join("'%s'" % g for g in BRACKET_TYPES)


def streaks(conn, owner_id, result, n=3):
    """The longest unbroken runs of one result, longest first.

    Walked in Python rather than solved in SQL: the gaps-and-islands query
    for this is harder to read than the loop, and there are 56 rows.

    A run does not cross a season boundary. Winning the last week of one year
    and the first of the next is two runs, not one -- there is a draft and a
    keeper deadline in between. Runs of one are dropped: "1 in a row" is not
    a streak, and Theo's third-longest win run is exactly that.
    """
    games = query(conn, """
        select season_year, week, result from game_log
        where owner_id = %s and game_type = 'regular'
        order by season_year, week
    """, (owner_id,))

    runs, live = [], None
    for g in games:
        if g["result"] != result:
            live = None
            continue
        if (live and live["season_year"] == g["season_year"]
                and live["last"] == g["week"] - 1):
            live["len"] += 1
            live["last"] = g["week"]
        else:
            live = {"season_year": g["season_year"], "first": g["week"],
                    "last": g["week"], "len": 1}
            runs.append(live)

    runs = [r for r in runs if r["len"] > 1]
    runs.sort(key=lambda r: (-r["len"], -r["season_year"], -r["first"]))
    return runs[:n]


def records_for(conn, keys, scope, params=(), n=5):
    return {k: query(conn, RECORD_SQL[k].format(scope=scope, n=n), params)
            for k in keys}


# --- crests of honour -----------------------------------------------------

# The order the case is read in, and what each group is called on the page.
CREST_GROUPS = (
    ("silverware", "Silverware"),
    ("scoring", "Over a season"),
    ("weekly", "Week by week"),
    ("keepers", "Keepers"),
    ("transactions", "The market"),
    ("rivalry", "Rivalry"),
    ("honours", "Honours"),
)


def week_slots(conn):
    """Every week the league has played, in order.

    Spells are built against this rather than against week numbers, so a
    title held in the last week of one season and the first of the next is
    one spell, and a week where nobody qualified breaks one.
    """
    return [(r["season_year"], r["week"]) for r in query(conn, """
        select distinct season_year, week from game_log
        where game_type = 'regular' order by season_year, week
    """)]


def spell_span(s):
    a, b = s["start"], s["end"]
    if (a["season_year"], a["week"]) == (b["season_year"], b["week"]):
        return "%d wk%d" % (a["season_year"], a["week"])
    if a["season_year"] == b["season_year"]:
        return "%d wk%d\u2013%d" % (a["season_year"], a["week"], b["week"])
    return "%d wk%d \u2013 %d wk%d" % (a["season_year"], a["week"],
                                       b["season_year"], b["week"])


def title_spells(rows, slots):
    """Weekly holder rows into unbroken spells, newest first.

    A title is recomputed after every week, so holding one for five weeks is
    five rows. Read as rows it says nothing; read as a spell it says Tom held
    the Captaincy in week nine of 2025, which is the thing worth knowing.

    Ties share a week, so two managers can be in a spell at once; each gets
    their own, which is right -- they held it together.
    """
    index = {s: i for i, s in enumerate(slots)}
    # A manager's own page passes rows that carry no owner_id, because every
    # one of them is theirs. They group as one owner, which is the truth.
    by_owner = {}
    for r in rows:
        if (r["season_year"], r["week"]) in index:
            by_owner.setdefault(r.get("owner_id"), []).append(r)

    spells = []
    for oid, mine in by_owner.items():
        mine.sort(key=lambda r: index[(r["season_year"], r["week"])])
        run = None
        for r in mine:
            i = index[(r["season_year"], r["week"])]
            if run is not None and i == run["at"] + 1:
                run["at"], run["end"], run["weeks"] = i, r, run["weeks"] + 1
            else:
                run = {"owner_id": oid, "username": r.get("username"),
                       "start": r, "end": r, "weeks": 1, "at": i}
                spells.append(run)
    for s in spells:
        s["span"] = spell_span(s)
        s["detail"] = s["end"]["detail"]
    spells.sort(key=lambda s: s["at"], reverse=True)
    return spells


def crest_case(catalogue, mine, slots=()):
    """A manager's case: what they hold, what they used to, what they won.

    Only what they have won. An unwon crest is a fact about the catalogue
    rather than about this manager, and twenty greyed-out names crowded out
    the eight that meant something.

    `mine` arrives newest first, so instances[0] is the most recent -- the one
    worth showing before the rest are expanded.

    Held rows come in two kinds. The one with no season is who holds the title
    now; the rest are end-of-season snapshots, written so a season page can
    ring its sigils with that year's titles. A manager with snapshots and no
    live row held the title once and lost it, which is worth saying: these
    change hands, and a page that only ever showed the current holder made
    every past one disappear.
    """
    won = {}
    for r in mine:
        won.setdefault(r["crest_id"], []).append(r)

    held, past, groups = [], [], []
    for category, label in CREST_GROUPS:
        row = []
        for c in catalogue:
            if c["category"] != category or c["crest_id"] not in won:
                continue
            got = won[c["crest_id"]]
            if c["standing"] == "held":
                # A title is three kinds of row. The one with no season says
                # who holds it now; the week rows are every week anyone held
                # it, which become spells; the season closes are what the
                # season pages ring their sigils from and are not shown here,
                # because a spell says everything they do and more.
                live = [r for r in got if r["season_year"] is None]
                spells = title_spells(
                    [r for r in got if r["week"] is not None], slots)
                if not live and not spells:
                    continue
                shown = live or [spells[0]["end"]]
                entry = dict(c, count=1, instances=shown, latest=shown[0],
                             spells=spells)
                (held if live else past).append(entry)
                continue
            row.append(dict(c, count=len(got), instances=got, latest=got[0]))
        if row:
            groups.append({"label": label, "crests": row})
    held.sort(key=lambda c: c["sort_order"])
    past.sort(key=lambda c: c["sort_order"])
    return held, past, groups


def title_lineage(rows):
    """One entry per held crest: who holds it now, and who held it at the
    close of each season.

    Held rows arrive in two kinds -- no season for the current holder, a year
    for each season's close -- so this is the same split crest_case() makes,
    turned on its side. That page asks what one manager holds; this one asks
    who has held each title, which is the question a history page is for.
    """
    out = {}
    for r in rows:
        t = out.setdefault(r["crest_id"], {
            "code": r["code"], "name": r["name"],
            "description": r["description"], "colour": r["colour"],
            "sort_order": r["sort_order"], "now": None, "by_year": {}})
        if r["season_year"] is None:
            t["now"] = r
        else:
            t["by_year"][r["season_year"]] = r
    return sorted(out.values(), key=lambda t: t["sort_order"])


def season_roll(rows):
    """A season's honour roll: the titles it closed holding, then what it
    handed out.

    The mirror of crest_case(). That groups one manager's crests and needs no
    name on them; this groups one season's and needs a name on every one. The
    same grid renders both, so the shape has to match: a list of held crests
    in sort order, then earned ones grouped by category.

    Weekly crests are excluded by the caller. Sixty-odd of them would bury
    the twelve that settle a season, and they belong beside the week that
    produced them.
    """
    held = sorted((r for r in rows if r["standing"] == "held"),
                  key=lambda r: r["sort_order"])
    groups = []
    for category, label in CREST_GROUPS:
        won = [r for r in rows
               if r["standing"] == "earned" and r["category"] == category]
        if won:
            groups.append({"label": label, "crests": won})
    return held, groups


def get_db():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def query(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


_owner_cache = {"at": 0.0, "rows": []}
_season_cache = {"at": 0.0, "rows": []}
_avatar_cache = {"at": 0.0, "map": {}}


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


def initials_for(username, last_name=None, override=None):
    """Override, else first + last initial, else the first two letters.

    last_name is null for everyone today, so most owners land on the last
    rung. Filling one in promotes that owner to real initials with no code
    change -- which is the whole reason the column exists.
    """
    if override and override.strip():
        return override.strip().upper()[:2]
    first = (username or "").strip()
    last = (last_name or "").strip()
    if first and last:
        return (first[0] + last[0]).upper()
    return first.upper()[:2]


def owner_avatars():
    """owner_id and lowercased username both key the same avatar entry.

    Almost every query on this site selects a username and nothing else --
    the head-to-head grid is keyed on username strings, and the stats views
    are fixed column lists. Looking colours up here at render time means no
    existing query or view has to change to carry the new columns.
    """
    now = time.time()
    if now - _avatar_cache["at"] > 300:
        try:
            with get_db() as conn:
                rows = query(conn, """
                    select owner_id, username, last_name,
                           avatar_bg, avatar_initials
                    from owners
                """)
                # The held crest each manager wears on their sigil. One
                # apiece: whoever holds two wears the lower sort_order, and
                # their page lists both. Looked up here rather than in every
                # query because a sigil is drawn from a username as often as
                # from an owner_id.
                #
                # season_year is null is the live holder. The rows that carry
                # a season are who held it when that season closed, and the
                # season page reaches for those instead.
                rings = {}
                for r in query(conn, """
                    select distinct on (oc.owner_id)
                           oc.owner_id, c.code, c.name, c.colour
                    from owner_crests oc
                    join crests c on c.crest_id = oc.crest_id
                    where c.standing = 'held' and oc.season_year is null
                    order by oc.owner_id, c.sort_order
                """):
                    rings[r["owner_id"]] = {"code": r["code"], "name": r["name"],
                                            "colour": r["colour"]}
            found = {}
            for r in rows:
                entry = {
                    "name": r["username"],
                    "bg": r["avatar_bg"],
                    "initials": initials_for(
                        r["username"], r["last_name"], r["avatar_initials"]),
                    "owner_id": r["owner_id"],
                    "ring": rings.get(r["owner_id"]),
                }
                found[r["owner_id"]] = entry
                found[r["username"].lower()] = entry
            _avatar_cache["map"] = found
            _avatar_cache["at"] = now
        except Exception:
            pass
    return _avatar_cache["map"]


def bust_owner_caches():
    """Anything that changes a username or an avatar invalidates both.

    The nav list and the avatar registry are keyed on usernames, so a
    rename has to clear them or the old name lingers for five minutes.
    """
    _avatar_cache["at"] = 0.0
    _owner_cache["at"] = 0.0


# Rings for one render, when the page is about a season rather than about
# now. A ContextVar rather than a template variable because av() is an
# environment global: macros.html is imported without context, so a macro
# inside it cannot see the calling template's variables, and threading a
# parameter through bracketdraw, recordlist, seedmark and the standings table
# would put the same argument in a dozen places for one page's sake.
#
# Set for the length of the render and reset after. Sync route handlers run
# in their own context, so one request's rings cannot leak into another's.
_page_rings = contextvars.ContextVar("page_rings", default=None)


def av(who, live=False):
    """Resolve a username, an owner_id or an owner row to avatar fields.

    Rows carrying someone else's name in a second column -- an opponent, a
    rival -- must pass that column explicitly rather than the whole row.

    `live` opts a sigil out of any season override. The nav bar's own chip
    uses it: that one is about who is signed in now, and it should not put
    2023's crown on your head because you happen to be reading 2023.
    """
    if isinstance(who, dict):
        who = who.get("owner_id") or who.get("username")
    key = who if isinstance(who, int) else (who or "").strip().lower()
    entry = owner_avatars().get(key)
    if not entry:
        label = "" if isinstance(who, int) else str(who or "")
        return {"name": label, "bg": "#33383D", "initials": initials_for(label),
                "owner_id": None, "ring": None}
    rings = None if live else _page_rings.get()
    if rings is None:
        return entry
    # A copy: the entry is the cached one, shared by every request.
    return dict(entry, ring=rings.get(entry["owner_id"]))


templates.env.globals["av"] = av


@app.get("/", response_class=HTMLResponse)
def home(request: Request, error: int = 0):
    if request.session.get("owner_id"):
        return RedirectResponse(url="/current", status_code=303)
    # Counted, not typed. "Four seasons" was hardcoded and went stale the
    # moment 2026 was entered.
    try:
        with get_db() as conn:
            tally = query(conn, """
                select
                  (select count(*) from season_results
                    where champion is not null)               as seasons,
                  (select count(*) from matchups
                    where team_a_points is not null)          as games
            """)[0]
    except Exception:
        tally = None
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"error": error, "tally": tally})


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
        # Every held crest, current holder and every season's close, in one
        # read. Ordered so title_lineage() gets them crest by crest with the
        # live row first.
        title_rows = query(conn, """
            select c.crest_id, c.code, c.name, c.description, c.colour,
                   c.sort_order, oc.season_year, oc.detail,
                   o.owner_id, o.username
            from owner_crests oc
            join crests c on c.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            where c.standing = 'held' and oc.week is null
            order by c.sort_order, oc.season_year desc nulls first
        """)
        standings = query(conn, """
            select * from owner_all_time_stats where seasons_played > 0
            order by win_pct desc, points_for desc
        """)
        h2h_rows = query(conn, "select * from owner_head_to_head")
        projections = query(conn, """
            select * from owner_projection_stats order by avg_vs_projection desc
        """)
        # Split, not merged. A 14-game regular season where every manager
        # plays is one thing; a playoff week where half the league is idle is
        # another, and a single list quietly let the second set the first's
        # records -- the all-time highest week was a semifinal.
        regular = records_for(conn, ALL_RECORDS, "game_type = 'regular'")
        postseason = records_for(conn, ALL_RECORDS, BRACKET_SCOPE)
    order = [s["username"] for s in standings]
    grid = {(r["username"], r["opponent_username"]): r for r in h2h_rows}
    return templates.TemplateResponse(
        request=request, name="history.html",
        context={"seasons": seasons, "standings": standings, "order": order,
                 "grid": grid, "projections": projections,
                 "titles": title_lineage(title_rows),
                 "title_years": [s["season_year"] for s in seasons],
                 "regular": regular, "postseason": postseason,
                 "record_names": RECORD_NAMES, "record_keys": ALL_RECORDS})


@app.get("/crests", response_class=HTMLResponse)
def crests(request: Request):
    """The whole catalogue, won or not.

    Every other page shows crests that have been earned. This one answers the
    question those pages cannot: what is there to win, and has anyone ever
    managed it. Three of them have never been awarded, which is worth being
    able to see.
    """
    with get_db() as conn:
        catalogue = query(conn, """
            select crest_id, code, name, description, category, standing,
                   colour, award_mode, sort_order
            from crests where active order by sort_order
        """)
        # Every award of every earned crest, newest first. Roughly three
        # hundred rows across the whole catalogue, which is one query and a
        # list per crest rather than a count and a shrug.
        awards = {}
        for r in query(conn, """
            select oc.crest_id, oc.season_year, oc.week, oc.detail,
                   o.owner_id, o.username
            from owner_crests oc
            join crests c on c.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            where c.standing = 'earned'
            order by oc.season_year desc nulls last,
                     oc.week desc nulls last, o.username
        """):
            awards.setdefault(r["crest_id"], []).append(r)

        # A title has a holder now and a list of who held it when each season
        # closed -- the same rows the season pages ring their sigils from.
        # A title has a holder now, and a history of every week anyone held
        # it. The season closes are skipped here: a spell says what they say
        # and says the weeks in between as well.
        slots = week_slots(conn)
        spells = {}
        for r in query(conn, """
            select oc.crest_id, oc.season_year, oc.week, oc.detail,
                   o.owner_id, o.username
            from owner_crests oc
            join crests c on c.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            where c.standing = 'held' and oc.week is not null
            order by oc.season_year, oc.week
        """):
            spells.setdefault(r["crest_id"], []).append(r)
        spells = {k: title_spells(v, slots) for k, v in spells.items()}

        holders, reigns = {}, {}
        for r in query(conn, """
            select oc.crest_id, oc.season_year, oc.detail,
                   o.owner_id, o.username
            from owner_crests oc
            join crests c on c.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            where c.standing = 'held' and oc.week is null
            order by oc.season_year desc nulls first
        """):
            if r["season_year"] is None:
                holders[r["crest_id"]] = r
            else:
                reigns.setdefault(r["crest_id"], []).append(r)

    titles = []
    for c in catalogue:
        if c["standing"] != "held":
            continue
        holder = holders.get(c["crest_id"])
        mine = spells.get(c["crest_id"], [])
        seasons = reigns.get(c["crest_id"], [])
        # Newest first, so the leading run belongs to whoever holds it now.
        # Those are not previous holders, they are the current one's own
        # earlier seasons -- and listing them put "2025 David" directly under
        # "Held by David". Skip them, and a title nobody has ever taken from
        # its holder correctly has no list at all.
        i = 0
        while (holder and i < len(seasons)
               and seasons[i]["owner_id"] == holder["owner_id"]):
            i += 1
        titles.append(dict(c, holder=holder, previous=seasons[i:],
                           spells=mine))
    groups = []
    for category, label in CREST_GROUPS:
        rows = [dict(c, awards=awards.get(c["crest_id"], []),
                     owners=len({a["owner_id"]
                                 for a in awards.get(c["crest_id"], [])}))
                for c in catalogue
                if c["category"] == category and c["standing"] == "earned"]
        if rows:
            groups.append({"label": label, "crests": rows})
    return templates.TemplateResponse(
        request=request, name="crests.html",
        context={"titles": titles, "groups": groups,
                 "unwon": sum(1 for c in catalogue
                              if c["standing"] == "earned"
                              and c["crest_id"] not in awards)})


@app.get("/preview", response_class=HTMLResponse)
def preview(request: Request):
    """A public page to send to someone who has not signed in yet.

    The top of /history -- champions and the all-time table -- and nothing
    else: no nav, no section rail, no way further in. Public on purpose, so
    the link works before the recipient has a session.
    """
    with get_db() as conn:
        seasons = query(conn, """
            select * from season_results where champion is not null
            order by season_year desc
        """)
        standings = query(conn, """
            select * from owner_all_time_stats where seasons_played > 0
            order by win_pct desc, points_for desc
        """)
    return templates.TemplateResponse(
        request=request, name="preview.html",
        context={"seasons": seasons, "standings": standings, "bare": True})


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
        # Split the way /history and a season page split theirs, through the
        # same shapes. Left merged, a manager's best week could be a playoff
        # game while the pages either side of this one had stopped counting
        # those.
        weeks_regular = records_for(conn, ("high", "low", "nailbiters",
                                           "heartbreaks"),
                                    "owner_id = %s and game_type = 'regular'",
                                    (oid,), 3)
        # Half the pool, capped at three, so the two lists cannot name the
        # same game twice. Chris has played two bracket games: a top three and
        # a bottom three of two games is the same pair printed backwards.
        bracket_games = query(conn, """
            select count(*) as n from game_log
            where owner_id = %s and """ + BRACKET_SCOPE, (oid,))[0]["n"]
        weeks_playoff = records_for(
            conn, ("high", "low", "nailbiters", "heartbreaks"),
            "owner_id = %s and " + BRACKET_SCOPE, (oid,),
            min(3, bracket_games // 2)) if bracket_games > 1 else None
        wins_streak = streaks(conn, oid, "W")
        losses_streak = streaks(conn, oid, "L")
        proj_rows = query(conn, """
            select * from owner_projection_stats where owner_id = %s
        """, (oid,))
        rivals = query(conn, """
            select r.season_year, ro.username as rival, ro.owner_id as rival_id,
                   r.score, r.source
            from rivalries r
            join owners ro on ro.owner_id = r.rival_owner_id
            where r.owner_id = %s order by r.season_year desc
        """, (oid,))
        keepers = query(conn, KEEPER_HISTORY_SQL + """
            where t.owner_id = %s
            order by ks.season_year desc, ks.cost_round
        """, (oid,))
        newest = _current_season(conn)
        crest_list = query(conn, """
            select crest_id, code, name, description, category, standing,
                   colour, sort_order
            from crests where active order by sort_order
        """)
        my_crests = query(conn, """
            select crest_id, season_year, week, detail from owner_crests
            where owner_id = %s
            order by season_year desc nulls first, week desc nulls first
        """, (oid,))
        # The keeper panel opens on the league's newest keeper season, so
        # every manager's page opens on the same year. Theo's last was 2023
        # and he has none since, so his falls back to his own newest rather
        # than opening on an empty panel.
        latest_keeper = query(conn, """
            select max(season_year) as y from keeper_selections
        """)[0]["y"]
        slots = week_slots(conn)
    held_crests, past_crests, crest_groups = crest_case(crest_list, my_crests, slots)

    keeper_groups = group_runs(keepers, "season_year")
    keeper_years = [g["key"] for g in keeper_groups]
    keeper_open = (latest_keeper if latest_keeper in keeper_years
                   else (keeper_years[0] if keeper_years else None))

    # Rivalries collapse into spells rather than a row per season. Twelve of
    # the thirteen managers have had the same rival every year -- the pairing
    # is solved from past meetings and comes out stable -- so a list by season
    # printed one name five times over. Only Curtis has two, Niall having
    # taken over when Theo retired.
    rival_spells = []
    for run in group_runs(rivals, "rival"):
        years = [r["season_year"] for r in run["rows"]]
        first, last = min(years), max(years)
        rival_spells.append({
            "rival": run["key"],
            "rival_id": run["rows"][0]["rival_id"],
            "span": str(first) if first == last else f"{first}–{last}",
            "last": last,
        })
    # "Current" only if the spell reaches the newest season. Theo's ran to
    # 2023 and he has not been in the league since; calling that current
    # would be a plain untruth on his page.
    rival_current = bool(rival_spells) and rival_spells[0]["last"] == newest

    return templates.TemplateResponse(
        request=request, name="team.html",
        context={"owner": owner, "seasons": seasons, "h2h": h2h,
                 "weeks_regular": weeks_regular, "weeks_playoff": weeks_playoff,
                 "wins_streak": wins_streak, "losses_streak": losses_streak,
                 "rival_spells": rival_spells,
                 "rival_current": rival_current,
                 "held_crests": held_crests, "past_crests": past_crests,
                 "crest_groups": crest_groups,
                 "keepers": keeper_groups, "keeper_open": keeper_open,
                 "projection": proj_rows[0] if proj_rows else None})


# --- the playoff field -------------------------------------------------
#
# Two byes, two more places on record, then the last two to the highest
# points for among everyone else. Checked against 2022-2025, where it
# reproduces each season's real bracket exactly -- including 2025, where Joey
# went in at 6-8 on points for and Borys stayed home at 7-7.
PLAYOFF_BYES = 2
PLAYOFF_ON_RECORD = 4
PLAYOFF_SPOTS = 6


def _record(t):
    """Wins, counting a tie as half. No season has produced one yet, so this
    has never mattered; it is here so that the first one does not silently
    rank a tied team below a team with the same number of wins."""
    return t["wins"] + 0.5 * (t["ties"] or 0)


def playoff_labels(standings, remaining):
    """Map team_id to a label for the standings table.

    `remaining` is regular season games still to play, per team_id.

    Once the regular season is over the field is settled, so the labels state
    the position outright. While it is running they say what is true now and
    what is already beyond reach, and the two are distinguished because
    "leading" and "cannot be caught" are different claims to make about
    someone's season.

    The clinch test uses wins alone and errs towards saying nothing: a berth
    is clinched when at most three others can still reach your win total, a
    bye when at most one can. Top four on record are always in, so being no
    worse than fourth is a berth whatever the points-for race does. Points
    for is deliberately left out of it -- bringing it in could only make the
    test claim more than it can prove.

    There is no "clinched wild card" for the same reason: wild cards are
    decided on points for, and a rival can always outscore you in the weeks
    left. It is only safe once the season is over, when it is also pointless.
    """
    played = sum(t["wins"] + t["losses"] + (t["ties"] or 0) for t in standings)
    if not played:
        return {}          # nothing to seed on, so no claims

    seeded = sorted(standings, key=lambda t: (-_record(t), -float(t["points_for"])))
    done = not any(remaining.get(t["team_id"], 0) for t in standings)

    # The last two places are the highest scorers among everyone left, which
    # is not the same as the next two on record and must not be taken from
    # the record order. 2025 is the case that proves it: Borys sat sixth at
    # 7-7 and Joey seventh at 6-8, and it was Joey who went, on points for.
    field = seeded[:PLAYOFF_ON_RECORD]
    wildcards = sorted(seeded[PLAYOFF_ON_RECORD:],
                       key=lambda t: -float(t["points_for"]))
    field += wildcards[:PLAYOFF_SPOTS - PLAYOFF_ON_RECORD]

    # mark: which glyph the standings table shows -- crown, shield, star.
    # firm: settled, nothing can undo it. A provisional mark is drawn as an
    # outline so the table does not present a lead as a guarantee.
    labels = {}
    for i, t in enumerate(field):
        mark = ("bye" if i < PLAYOFF_BYES
                else "berth" if i < PLAYOFF_ON_RECORD
                else "wild")

        if done:
            text = ("Bye" if mark == "bye"
                    else "Playoff team" if mark == "berth"
                    else "Wild card")
            labels[t["team_id"]] = {"text": text, "firm": True, "mark": mark}
            continue

        # Worst case for this team against the best case for everyone else.
        floor = _record(t)
        rivals = sum(1 for o in standings
                     if o["team_id"] != t["team_id"]
                     and _record(o) + remaining.get(o["team_id"], 0) >= floor)

        if mark == "bye":
            firm = rivals <= PLAYOFF_BYES - 1
            text = "Clinched bye" if firm else "On pace for bye"
        elif mark == "berth":
            firm = rivals <= PLAYOFF_ON_RECORD - 1
            text = "Clinched playoffs" if firm else "Playoff team"
        else:
            # Never firm: a wild card rests on points for, which the weeks
            # left can always overturn.
            firm, text = False, "Wild card"
        labels[t["team_id"]] = {"text": text, "firm": firm, "mark": mark}
    return labels


def seed_key(labels, standings):
    """The legend under the standings, built from the marks actually on the
    page. Generated rather than written out so it can never describe a state
    the table is not showing -- a finished season has no "on pace" row."""
    order = {"bye": 0, "berth": 1, "wild": 2}
    seen, key = set(), []
    for t in standings:
        seed = labels.get(t["team_id"])
        if not seed:
            continue
        ident = (seed["mark"], seed["firm"], seed["text"])
        if ident not in seen:
            seen.add(ident)
            key.append(seed)
    return sorted(key, key=lambda s: (order[s["mark"]], not s["firm"]))


def _victor(g):
    """Who won a played game, by owner name. None while it is unplayed, and
    None on an exact tie, which the data cannot resolve on its own."""
    a, b = g["points_a"], g["points_b"]
    if a is None or b is None or a == b:
        return None
    return g["owner_a"] if a > b else g["owner_b"]


def _draw(opening, middle, final, third, fifth, names):
    """Assemble one bracket from its three rounds.

    Both sides of the playoffs have the same shape: four opening rows, two of
    which carry a null opponent and are byes for the higher seeds; two games
    in the middle round; one to settle the side. Two more games decide the
    places below it.

    Nothing records which bye feeds which middle game, so it is worked out
    from who turns up: a middle game holds one bye team and one winner from
    the opening round. Before those are played there is nothing to read it
    from, and the halves pair in the order the rows arrive -- the two sides of
    a bracket are interchangeable until somebody plays.
    """
    byes = [g for g in opening if not g["team_b"]]
    duels = [g for g in opening if g["team_b"]]

    halves, spare_byes, spare_duels = [], list(byes), list(duels)
    for semi in middle:
        here = {semi["owner_a"], semi["owner_b"]}
        bye = next((b for b in spare_byes if b["owner_a"] in here), None)
        duel = next((d for d in spare_duels if _victor(d) in here), None)
        if bye:
            spare_byes.remove(bye)
        if duel:
            spare_duels.remove(duel)
        halves.append({"bye": bye, "duel": duel, "semi": semi})

    # Whatever the middle round could not account for -- because it has not
    # been played, or because a tie left no winner to trace.
    while spare_byes or spare_duels:
        halves.append({"bye": spare_byes.pop(0) if spare_byes else None,
                       "duel": spare_duels.pop(0) if spare_duels else None,
                       "semi": None})

    return {
        "halves": halves,
        "final": final,
        # Every round has a result. Nothing reaches the last game until the
        # rounds before it are played, so the one game answers for all three.
        "settled": bool(final and _victor(final)),
        "third": third,
        "fifth": fifth,
        "names": names,
    }


def _one(rows):
    return rows[0] if rows else None


def playoff_brackets(games):
    """Both sides of the playoffs, drawn from the week 15-17 rows.

    The championship side is named by game type throughout. The consolation
    side is not: its first two rounds are both `consolation` and are told
    apart by week, which is why it is picked out here rather than by a lookup.
    """
    rounds = {}
    for g in games:
        if g["game_type"] != "regular":
            rounds.setdefault(g["game_type"], []).append(g)

    out = {"championship": None, "consolation": None}

    if rounds.get("quarterfinal"):
        out["championship"] = _draw(
            rounds["quarterfinal"], rounds.get("semifinal", []),
            _one(rounds.get("championship", [])),
            _one(rounds.get("third_place", [])),
            _one(rounds.get("fifth_place", [])),
            ("Quarterfinals", "Semifinals", "Final",
             "Third place", "Fifth place"))

    cons = rounds.get("consolation", [])
    if cons:
        opening_week = min(g["week"] for g in cons)
        out["consolation"] = _draw(
            [g for g in cons if g["week"] == opening_week],
            [g for g in cons if g["week"] != opening_week],
            _one(rounds.get("seventh_place", [])),
            _one(rounds.get("ninth_place", [])),
            _one(rounds.get("eleventh_place", [])),
            ("First round", "Second round", "Seventh place",
             "Ninth place", "Eleventh place"))

    return out


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
            order by final_rank nulls last,
                     wins + 0.5 * coalesce(ties, 0) desc, points_for desc
        """, (year,))
        # Scheduled regular season games per team, so the clinch test knows
        # how many are left. Counted from matchups rather than assuming 14,
        # so a half-built schedule does not make everyone look eliminated.
        remaining = {r["team_id"]: r["left"] for r in query(conn, """
            select t.team_id,
                   count(m.matchup_id) filter (where m.game_type = 'regular')
                     - (s.wins + s.losses + coalesce(s.ties, 0)) as left
            from teams t
            join team_season_stats s on s.team_id = t.team_id
            left join matchups m
              on m.season_year = t.season_year
             and (m.team_a_id = t.team_id or m.team_b_id = t.team_id)
            where t.season_year = %s
            group by t.team_id, s.wins, s.losses, s.ties
        """, (year,))}
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
        # Regular season only, and game_type rather than week <= 14 so the
        # filter states what it means and survives a season of another
        # length. /history reports the playoff records separately; here a
        # single season's playoff sample is a handful of games, so the page
        # shows the 14-game field where everyone plays.
        records = records_for(conn, ("high", "blowouts", "nailbiters"),
                              "season_year = %s and game_type = 'regular'",
                              (year,), 3)
        # A season with no scores yet has nothing to rank, and an empty
        # Records section would still claim a spot in the section nav.
        if not records["high"]:
            records = None
        # Rivalry pairings, so a week can be recognised rather than declared.
        # Written both ways round in the table, so this set already holds
        # both orientations and the matchup can be looked up as it comes.
        rival_pairs = {(r["a"], r["b"]) for r in query(conn, """
            select oa.username as a, ob.username as b
            from rivalries r
            join owners oa on oa.owner_id = r.owner_id
            join owners ob on ob.owner_id = r.rival_owner_id
            where r.season_year = %s
        """, (year,))}
        # The season's crests, and who took them. Held rows carrying this
        # season are who held each title when it closed, which is not who
        # holds it now -- see migration 038. Weekly crests are left out here
        # and drawn beside their own week instead.
        crest_rows = query(conn, """
            select c.code, c.name, c.description, c.category, c.standing,
                   c.colour, c.sort_order, oc.detail,
                   o.owner_id, o.username
            from owner_crests oc
            join crests c on c.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            where oc.season_year = %s and oc.week is null
            order by c.sort_order, o.username
        """, (year,))
        # The other half of the same table: crests settled by one week's
        # scores. They go under that week's results rather than into the
        # honour roll, where sixty-odd of them would bury the twelve that
        # settle a season and none would be next to the score that won it.
        week_crests = {}
        for r in query(conn, """
            select c.code, c.category, c.name, c.description, oc.week,
                   oc.detail, o.owner_id, o.username
            from owner_crests oc
            join crests c on c.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            where oc.season_year = %s and oc.week is not null
              and c.standing = 'earned'
            order by oc.week, c.sort_order, o.username
        """, (year,)):
            week_crests.setdefault(r["week"], []).append(r)
        keepers = query(conn, KEEPER_HISTORY_SQL + """
            where ks.season_year = %s
            order by o.username, ks.cost_round
        """, (year,))
    weeks = []
    for g in games:
        if not weeks or weeks[-1]["week"] != g["week"]:
            weeks.append({"week": g["week"], "games": [], "played": False})
        g["rival"] = (g["owner_a"], g["owner_b"]) in rival_pairs
        weeks[-1]["games"].append(g)
        if g["points_a"] is not None:
            weeks[-1]["played"] = True

    # Rivalry week is the week where every game is a rival meeting, which is
    # read off the fixtures rather than pinned to a number. The schedule
    # generator puts it in week 10, but a week that merely happens to be
    # numbered 10 is not rivalry week: the pre-2026 schedules came from Yahoo
    # and know nothing about rivalries. Across all five seasons this is true
    # of exactly one week -- 2026's tenth -- and of no other. A lone rival
    # meeting elsewhere, of which there are several, does not qualify.
    for w in weeks:
        w["rivalry"] = len(w["games"]) > 1 and all(g["rival"] for g in w["games"])

    # Open on the last week that has scores, so a season in progress lands on
    # what just happened rather than on week 1. Before a ball is kicked, and
    # once the season is over, that is the first and last week respectively.
    played = [w["week"] for w in weeks if w["played"]]
    open_week = played[-1] if played else (weeks[0]["week"] if weeks else None)

    seeds = playoff_labels(standings, remaining)
    brackets = playoff_brackets(games)
    titles, crest_groups = season_roll(crest_rows)

    # Every sigil on the page wears the title its manager held when this
    # season closed. `titles` is already in sort_order, so setdefault keeps
    # the same one the live ring would -- lowest first, page lists the rest.
    #
    # A season still being played has no snapshot to dress itself in, and the
    # empty map is left unset rather than passed: today's rings are the right
    # answer for today's season.
    rings = {}
    for c in titles:
        rings.setdefault(c["owner_id"], {"code": c["code"], "name": c["name"],
                                         "colour": c["colour"]})
    token = _page_rings.set(rings) if rings else None
    try:
        return templates.TemplateResponse(
            request=request, name="season.html",
            context={"s": head[0], "standings": standings, "weeks": weeks,
                     "records": records, "open_week": open_week,
                     "seeds": seeds, "seed_key": seed_key(seeds, standings),
                     "brackets": brackets, "titles": titles,
                     "crest_groups": crest_groups, "week_crests": week_crests,
                     "keepers": group_runs(keepers, "username")})
    finally:
        if token is not None:
            _page_rings.reset(token)


# Order the rivalry weights are shown in on /rules, loosest to fiercest.
RIVAL_WEIGHT_LABELS = [
    ("regular", "Regular season"),
    ("consolation", "Consolation"),
    ("eleventh_place", "Eleventh place"),
    ("ninth_place", "Ninth place"),
    ("seventh_place", "Seventh place"),
    ("fifth_place", "Fifth place"),
    ("quarterfinal", "Quarterfinal"),
    ("semifinal", "Semifinal"),
    ("third_place", "Third place"),
    ("championship", "Championship"),
]


@app.get("/rules", response_class=HTMLResponse)
def rules(request: Request):
    """The rivalry weights and league size come from the code and the
    seasons row, not from prose, so the page cannot drift from behaviour."""
    with get_db() as conn:
        rows = query(conn, """
            select season_year, team_count, keeper_count
            from seasons order by season_year desc limit 1
        """)
    s = rows[0] if rows else {"season_year": None, "team_count": 12,
                              "keeper_count": 3}
    # %g so 10.0 reads as 10 while 2.5 stays 2.5.
    def n(v):
        return f"{v:g}"

    return templates.TemplateResponse(
        request=request, name="rules.html",
        context={"s": s,
                 "weights": [(label, n(RIVAL_WEIGHTS[key]))
                             for key, label in RIVAL_WEIGHT_LABELS],
                 "close_bonus": n(RIVAL_CLOSE_BONUS),
                 "close_margin": n(RIVAL_CLOSE_MARGIN),
                 "prior_bonus": n(RIVAL_PRIOR_BONUS)})


def _colour_holders(rows, skip=None):
    """Which other owners already sit on each colour.

    Colours are not unique, so this is a hint in the picker rather than a
    restriction: you can see who you are about to match before you do it.
    """
    holders = {}
    for r in rows:
        if skip is not None and r["owner_id"] == skip:
            continue
        holders.setdefault(r["avatar_bg"], []).append(r["username"])
    return holders


def _current_season(conn):
    return query(conn, "select max(season_year) as y from seasons")[0]["y"]


def group_runs(rows, key):
    """Consecutive rows sharing a key, in the order the query returned them.

    The query does the ordering; this only breaks it into runs.
    """
    out = []
    for r in rows:
        if not out or out[-1]["key"] != r[key]:
            out.append({"key": r[key], "rows": []})
        out[-1]["rows"].append(r)
    return out


# keeper_selections is the record of what was actually kept. It agrees with
# the is_keeper flags on draft_picks and carries the cost and the contract,
# which the flags do not.
KEEPER_HISTORY_SQL = """
    select ks.season_year, ks.cost_round, ks.keeper_year,
           o.owner_id, o.username,
           pl.full_name, pl.position,
           kc.contract_years, kc.status as contract_status
    from keeper_selections ks
    join teams   t  on t.team_id   = ks.team_id
    join owners  o  on o.owner_id  = t.owner_id
    join players pl on pl.player_id = ks.player_id
    left join keeper_contracts kc on kc.contract_id = ks.contract_id
"""


def _owner_form(conn, oid):
    """One owner, their current-season team, and the palette context."""
    everyone = query(conn, """
        select owner_id, username, last_name, email, is_admin, is_retired,
               avatar_bg, avatar_initials
        from owners order by username
    """)
    me = next((r for r in everyone if r["owner_id"] == oid), None)
    if not me:
        raise HTTPException(status_code=404, detail="No such owner")
    season = _current_season(conn)
    teams = query(conn, """
        select team_id, team_name from teams
        where owner_id = %s and season_year = %s
    """, (oid, season))
    return {
        "me": me,
        "season": season,
        "team": teams[0] if teams else None,
        "palette": AVATAR_PALETTE,
        "holders": _colour_holders(everyone, skip=oid),
    }


def _league_size(conn, season):
    """How many active owners the league holds. Same source draft_prep uses."""
    rows = query(conn, "select team_count from seasons where season_year = %s",
                 (season,))
    return rows[0]["team_count"] if rows else 12


def _active_count(conn):
    return query(conn, """
        select count(*) as n from owners where not is_retired
    """)[0]["n"]


def _taken(conn, column, value, oid):
    """Is this username or email already another owner's?"""
    rows = query(conn, f"""
        select username from owners
        where {column} is not null and lower({column}) = lower(%s)
          and owner_id <> %s
    """, (value, oid))
    return rows[0]["username"] if rows else None


def _save_sigil(cur, oid, bg, initials):
    cur.execute("""
        update owners
        set avatar_bg = %s, avatar_initials = %s, updated_at = now()
        where owner_id = %s
    """, (bg, initials or None, oid))


def _clean_initials(raw):
    return (raw or "").strip().upper()[:2]


# ---- profile ----

@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    with get_db() as conn:
        ctx = _owner_form(conn, request.session["owner_id"])
    return templates.TemplateResponse(
        request=request, name="profile.html", context=ctx)


@app.post("/profile")
async def profile_sigil(request: Request):
    from urllib.parse import quote
    oid = request.session["owner_id"]
    form = await request.form()
    bg = (form.get("avatar_bg") or "").strip().upper()

    if bg not in AVATAR_HEXES:
        return RedirectResponse(
            url="/profile?error=" + quote("That is not one of the league colours."),
            status_code=303)

    with get_db() as conn:
        with conn.cursor() as cur:
            _save_sigil(cur, oid, bg, _clean_initials(form.get("avatar_initials")))
        conn.commit()
    bust_owner_caches()

    return RedirectResponse(
        url="/profile?msg=" + quote("Sigil saved"), status_code=303)


@app.post("/profile/details")
async def profile_details(request: Request):
    from urllib.parse import quote
    oid = request.session["owner_id"]
    form = await request.form()
    username = (form.get("username") or "").strip()
    last_name = (form.get("last_name") or "").strip()
    team_name = (form.get("team_name") or "").strip()
    retired = form.get("is_retired") is not None

    def bad(msg):
        return RedirectResponse(
            url="/profile?error=" + quote(msg), status_code=303)

    if not username:
        return bad("A display name is required.")

    with get_db() as conn:
        clash = _taken(conn, "username", username, oid)
        if clash:
            return bad(f"{clash} already uses that name.")

        season = _current_season(conn)
        with conn.cursor() as cur:
            cur.execute("""
                update owners
                set username = %s, last_name = %s, is_retired = %s,
                    updated_at = now()
                where owner_id = %s
            """, (username, last_name or None, retired, oid))
            # No team row means no current-season entry, so nothing to rename.
            if team_name:
                cur.execute("""
                    update teams set team_name = %s, updated_at = now()
                    where owner_id = %s and season_year = %s
                """, (team_name, oid, season))
        conn.commit()

    request.session["username"] = username
    bust_owner_caches()

    return RedirectResponse(
        url="/profile?msg=" + quote("Details saved"), status_code=303)


@app.post("/profile/email")
async def profile_email(request: Request):
    from urllib.parse import quote
    oid = request.session["owner_id"]
    form = await request.form()
    email = (form.get("email") or "").strip()
    confirm = (form.get("email_confirm") or "").strip()

    def bad(msg):
        return RedirectResponse(
            url="/profile?error=" + quote(msg), status_code=303)

    # Signing in is an email match and there is no reset flow, so a typo
    # here locks the owner out until an admin fixes it. Hence the retype.
    if email.lower() != confirm.lower():
        return bad("The two addresses do not match.")
    if not email:
        return bad("Clearing your address would lock you out. Ask an admin.")

    with get_db() as conn:
        clash = _taken(conn, "email", email, oid)
        if clash:
            return bad(f"{clash} already uses that address.")
        with conn.cursor() as cur:
            cur.execute("""
                update owners set email = %s, updated_at = now()
                where owner_id = %s
            """, (email, oid))
        conn.commit()

    return RedirectResponse(
        url="/profile?msg=" + quote("Sign-in address saved"), status_code=303)


# ---- admin: everyone at a glance, then one at a time ----

@app.get("/admin/owners", response_class=HTMLResponse)
def admin_owners(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")

    with get_db() as conn:
        season = _current_season(conn)
        owners = query(conn, """
            select o.owner_id, o.username, o.last_name, o.email,
                   o.is_admin, o.is_retired, o.avatar_bg, o.avatar_initials,
                   t.team_name
            from owners o
            left join teams t
              on t.owner_id = o.owner_id and t.season_year = %s
            order by o.is_retired, o.username
        """, (season,))
        active, size = _active_count(conn), _league_size(conn, season)
    holders = _colour_holders(owners)
    # Preselect a colour nobody holds, so a new owner is distinct by default.
    free = next((h for _, h in AVATAR_PALETTE if h not in holders),
                AVATAR_PALETTE[0][1])
    return templates.TemplateResponse(
        request=request, name="admin_owners.html",
        context={"owners": owners, "palette": AVATAR_PALETTE,
                 "season": season, "holders": holders, "free_bg": free,
                 "active": active, "size": size, "can_add": active < size})


@app.post("/admin/owners")
async def admin_owners_save(request: Request):
    """The quick pass: team name, colour and initials for everyone at once."""
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()

    def bad(msg):
        return RedirectResponse(
            url="/admin/owners?error=" + quote(msg), status_code=303)

    with get_db() as conn:
        season = _current_season(conn)
        # Only owners with a row for this season have a name to rename, and
        # team_name is not null, so a blank is refused rather than skipped.
        named = {r["owner_id"]: r["username"] for r in query(conn, """
            select t.owner_id, o.username
            from teams t join owners o on o.owner_id = t.owner_id
            where t.season_year = %s
        """, (season,))}

        updates = []
        for key in form.keys():
            if not key.startswith("bg_"):
                continue
            oid = int(key.split("_", 1)[1])
            bg = (form.get(key) or "").strip().upper()
            if bg not in AVATAR_HEXES:
                return bad("That is not one of the league colours.")
            team = (form.get(f"team_{oid}") or "").strip()
            if oid in named and not team:
                return bad(f"{named[oid]} needs a team name.")
            updates.append((oid, bg, _clean_initials(form.get(f"initials_{oid}")),
                            team if oid in named else None))

        with conn.cursor() as cur:
            for oid, bg, initials, team in updates:
                _save_sigil(cur, oid, bg, initials)
                if team:
                    cur.execute("""
                        update teams set team_name = %s, updated_at = now()
                        where owner_id = %s and season_year = %s
                    """, (team, oid, season))
        conn.commit()
    bust_owner_caches()

    return RedirectResponse(
        url="/admin/owners?msg=" + quote("Owners saved"), status_code=303)


@app.post("/admin/owners/new")
async def admin_owner_new(request: Request):
    """Create the owner record only.

    Deliberately no `teams` row: rivalries and the schedule both take their
    participants from teams for the season, and both need an even count.
    Putting someone into a season is a separate job.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    username = (form.get("username") or "").strip()
    last_name = (form.get("last_name") or "").strip()
    email = (form.get("email") or "").strip()
    bg = (form.get("avatar_bg") or "").strip().upper()
    is_admin = form.get("is_admin") is not None

    def bad(msg):
        return RedirectResponse(
            url="/admin/owners?error=" + quote(msg), status_code=303)

    if not username:
        return bad("A display name is required.")
    if bg not in AVATAR_HEXES:
        return bad("That is not one of the league colours.")

    with get_db() as conn:
        # The league is a fixed size. Someone has to leave before someone
        # joins, so this is checked here and not only hidden in the form.
        size = _league_size(conn, _current_season(conn))
        active = _active_count(conn)
        if active >= size:
            return bad(f"The league is full at {size}. "
                       f"Retire an owner before adding one.")

        clash = _taken(conn, "username", username, 0)
        if clash:
            return bad(f"{clash} already uses that name.")
        if email:
            clash = _taken(conn, "email", email, 0)
            if clash:
                return bad(f"{clash} already uses that address.")

        with conn.cursor() as cur:
            cur.execute("""
                insert into owners
                    (username, last_name, email, is_admin, avatar_bg, created_by)
                values (%s, %s, %s, %s, %s, %s)
                returning owner_id
            """, (username, last_name or None, email or None, is_admin, bg,
                  request.session.get("owner_id")))
            new_id = cur.fetchone()["owner_id"]
        conn.commit()
    bust_owner_caches()

    return RedirectResponse(
        url=f"/admin/owners/{new_id}?msg=" + quote(f"{username} added"),
        status_code=303)


@app.get("/admin/owners/{oid}", response_class=HTMLResponse)
def admin_owner_edit(request: Request, oid: int):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    with get_db() as conn:
        ctx = _owner_form(conn, oid)
        ctx["admin_count"] = query(conn, """
            select count(*) as n from owners where is_admin
        """)[0]["n"]
    return templates.TemplateResponse(
        request=request, name="admin_owner_edit.html", context=ctx)


@app.post("/admin/owners/{oid}")
async def admin_owner_edit_save(request: Request, oid: int):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    username = (form.get("username") or "").strip()
    last_name = (form.get("last_name") or "").strip()
    email = (form.get("email") or "").strip()
    team_name = (form.get("team_name") or "").strip()
    bg = (form.get("avatar_bg") or "").strip().upper()
    is_admin = form.get("is_admin") is not None
    retired = form.get("is_retired") is not None

    def bad(msg):
        return RedirectResponse(
            url=f"/admin/owners/{oid}?error=" + quote(msg), status_code=303)

    if not username:
        return bad("A display name is required.")
    if bg not in AVATAR_HEXES:
        return bad("That is not one of the league colours.")

    with get_db() as conn:
        clash = _taken(conn, "username", username, oid)
        if clash:
            return bad(f"{clash} already uses that name.")
        if email:
            clash = _taken(conn, "email", email, oid)
            if clash:
                return bad(f"{clash} already uses that address.")

        # Bringing someone back is the other way to overfill the league.
        was = query(conn, """
            select is_retired from owners where owner_id = %s
        """, (oid,))
        if not was:
            raise HTTPException(status_code=404, detail="No such owner")
        if was[0]["is_retired"] and not retired:
            size = _league_size(conn, _current_season(conn))
            if _active_count(conn) >= size:
                return bad(f"The league is full at {size}. "
                           f"Retire an owner before bringing one back.")

        # Removing the last admin would leave nobody able to reach this page.
        if not is_admin:
            others = query(conn, """
                select count(*) as n from owners
                where is_admin and owner_id <> %s
            """, (oid,))[0]["n"]
            if others == 0:
                return bad("That is the last admin. Promote someone first.")

        season = _current_season(conn)
        with conn.cursor() as cur:
            cur.execute("""
                update owners
                set username = %s, last_name = %s, email = %s,
                    is_admin = %s, is_retired = %s, updated_at = now()
                where owner_id = %s
            """, (username, last_name or None, email or None,
                  is_admin, retired, oid))
            _save_sigil(cur, oid, bg, _clean_initials(form.get("avatar_initials")))
            if team_name:
                cur.execute("""
                    update teams set team_name = %s, updated_at = now()
                    where owner_id = %s and season_year = %s
                """, (team_name, oid, season))
        conn.commit()

    # An admin editing themselves needs the session to follow.
    if oid == request.session.get("owner_id"):
        request.session["username"] = username
        request.session["is_admin"] = is_admin
    bust_owner_caches()

    return RedirectResponse(
        url="/admin/owners?msg=" + quote(f"{username} saved"), status_code=303)


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

        # Shown so the weighting is checkable rather than taken on trust.
        _, _, finishes, weights, newcomers = lottery_entry(conn, season)

    for r in rows:
        r["finish"] = finishes.get(r["owner_id"])
        r["ballots"] = weights.get(r["owner_id"])
        r["is_new"] = r["owner_id"] in newcomers
    ballot_total = sum(weights.values())

    taken = {r["slot"]: r["username"] for r in rows if r["slot"]}
    on_clock = next((r for r in rows if r["is_on_the_clock"]), None)
    my_turn = bool(on_clock and on_clock["owner_id"] == me)

    return templates.TemplateResponse(
        request=request, name="draft_order.html",
        context={"years": years, "season": season, "rows": rows,
                 "slots": list(range(1, team_count + 1)), "taken": taken,
                 "on_clock": on_clock, "my_turn": my_turn,
                 "ballot_total": ballot_total, "prev_season": season - 1,
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


# Ballots by last season's finish: 1 for a top-three finish, 2 for 4th-6th,
# 3 for 7th and below. Twelve managers put 27 ballots in the hat.
LOTTERY_TIERS = ((3, 1), (6, 2))
LOTTERY_TAIL_BALLOTS = 3


def lottery_ballots(finish):
    """None means no finish to go on -- a new manager, who does not draw."""
    if finish is None:
        return None
    for limit, ballots in LOTTERY_TIERS:
        if finish <= limit:
            return ballots
    return LOTTERY_TAIL_BALLOTS


def draw_lottery(ballots, rng):
    """Pull names from the hat weighted by ballots, without replacement.

    Once a manager is drawn they are placed, and their remaining ballots
    leave the hat with them.
    """
    pool = list(ballots)
    order = []
    while pool:
        total = sum(ballots[o] for o in pool)
        pick = rng.randrange(total)
        run = 0
        for o in pool:
            run += ballots[o]
            if pick < run:
                order.append(o)
                pool.remove(o)
                break
    return order


def lottery_entry(conn, season):
    """Who is drawing, how many ballots each, and who skips the draw."""
    entrants = query(conn, """
        select t.owner_id, o.username
        from teams t join owners o on o.owner_id = t.owner_id
        where t.season_year = %s order by o.username
    """, (season,))
    finishes = {r["owner_id"]: r["final_rank"] for r in query(conn, """
        select t.owner_id, fs.final_rank
        from final_standings fs
        join teams t on t.team_id = fs.team_id
        where fs.season_year = %s
    """, (season - 1,))}

    ids = [e["owner_id"] for e in entrants]
    names = {e["owner_id"]: e["username"] for e in entrants}

    # With no ranked previous season there is nothing to weight by, and
    # nobody is meaningfully new, so everyone draws on equal terms.
    if not finishes:
        return ids, names, {}, {o: 1 for o in ids}, {}

    newcomers = [o for o in ids if finishes.get(o) is None]
    weights = {o: lottery_ballots(finishes[o]) for o in ids if o not in newcomers}
    return ids, names, finishes, weights, newcomers


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
        ids, names, _, weights, newcomers = lottery_entry(conn, season)
        if not ids:
            return RedirectResponse(
                url=f"/draft-order?season={season}&error=" +
                    quote(f"No teams entered for {season}."), status_code=303)

        rng = _random.Random()
        # A new manager skips the draw entirely and chooses first. More than
        # one, and they settle it between themselves at random.
        newcomers = list(newcomers)
        rng.shuffle(newcomers)
        order = newcomers + draw_lottery(weights, rng)

        with conn.cursor() as cur:
            cur.execute("delete from draft_order where season_year = %s", (season,))
            for position, oid in enumerate(order, start=1):
                cur.execute("""
                    insert into draft_order
                        (season_year, owner_id, lottery_position)
                    values (%s, %s, %s)
                """, (season, oid, position))
        conn.commit()

    msg = f"Lottery drawn for {len(order)} managers"
    if newcomers:
        msg += f". {', '.join(names[o] for o in newcomers)} first, as new"
    return RedirectResponse(
        url=f"/draft-order?season={season}&msg=" + quote(msg), status_code=303)


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







RIVAL_WEIGHTS = {
    "regular": 1.0,
    "consolation": 2.0,
    "eleventh_place": 2.5,
    "ninth_place": 2.5,
    "seventh_place": 3.0,
    "fifth_place": 3.5,
    "quarterfinal": 5.0,
    "semifinal": 6.0,
    "third_place": 6.0,
    "championship": 10.0,
}
RIVAL_PRIOR_BONUS = 4.0
RIVAL_CLOSE_BONUS = 2.0
RIVAL_CLOSE_MARGIN = 10.0


def rivalry_scores(conn, season):
    """Pairwise rivalry score for every pair of owners active in `season`."""
    owners = query(conn, """
        select t.owner_id, o.username
        from teams t join owners o on o.owner_id = t.owner_id
        where t.season_year = %s order by o.username
    """, (season,))
    ids = [o["owner_id"] for o in owners]
    names = {o["owner_id"]: o["username"] for o in owners}

    games = query(conn, """
        select g.owner_id, g.opponent_owner_id, g.game_type,
               abs(g.points_for - g.points_against) as margin
        from game_log g
        where g.season_year <= %s
    """, (season,))

    priors = query(conn, """
        select owner_id, rival_owner_id, count(*) as n
        from rivalries where season_year < %s
        group by owner_id, rival_owner_id
    """, (season,))

    score = {}
    detail = {}
    for g in games:
        a, b = g["owner_id"], g["opponent_owner_id"]
        if a not in names or b not in names or a >= b:
            continue
        w = RIVAL_WEIGHTS.get(g["game_type"], 1.0)
        if g["margin"] is not None and float(g["margin"]) <= RIVAL_CLOSE_MARGIN:
            w += RIVAL_CLOSE_BONUS
        key = (a, b)
        score[key] = score.get(key, 0.0) + w
        d = detail.setdefault(key, {"games": 0, "playoff": 0, "close": 0})
        d["games"] += 1
        if g["game_type"] != "regular":
            d["playoff"] += 1
        if g["margin"] is not None and float(g["margin"]) <= RIVAL_CLOSE_MARGIN:
            d["close"] += 1

    for p in priors:
        a, b = sorted((p["owner_id"], p["rival_owner_id"]))
        if a in names and b in names:
            key = (a, b)
            score[key] = score.get(key, 0.0) + RIVAL_PRIOR_BONUS * p["n"]
            detail.setdefault(key, {"games": 0, "playoff": 0, "close": 0})
            detail[key]["prior"] = p["n"]

    return ids, names, score, detail


def best_pairing(ids, score):
    """Highest total score across every perfect matching. 12 owners = 10,395."""
    best = {"total": None, "pairs": None}

    def walk(remaining, pairs, total):
        if not remaining:
            if best["total"] is None or total > best["total"]:
                best["total"] = total
                best["pairs"] = list(pairs)
            return
        a = remaining[0]
        for i in range(1, len(remaining)):
            b = remaining[i]
            key = (a, b) if a < b else (b, a)
            pairs.append((a, b))
            walk(remaining[1:i] + remaining[i + 1:], pairs,
                 total + score.get(key, 0.0))
            pairs.pop()

    if len(ids) % 2:
        return None, None
    walk(list(ids), [], 0.0)
    return best["pairs"], best["total"]


@app.get("/admin/rivals", response_class=HTMLResponse)
def admin_rivals(request: Request, season: int = 0, preview: int = 0):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")

    with get_db() as conn:
        years = query(conn, "select season_year from seasons order by season_year desc")
        if not season:
            season = years[0]["season_year"]

        current = query(conn, """
            select r.owner_id, o.username, r.rival_owner_id, ro.username as rival,
                   r.score, r.source
            from rivalries r
            join owners o on o.owner_id = r.owner_id
            join owners ro on ro.owner_id = r.rival_owner_id
            where r.season_year = %s order by o.username
        """, (season,))

        ids, names, score, detail = rivalry_scores(conn, season)

    proposed = []
    total = None
    if preview:
        pairs, total = best_pairing(ids, score)
        for a, b in (pairs or []):
            key = (a, b) if a < b else (b, a)
            d = detail.get(key, {})
            proposed.append({
                "a": names[a], "b": names[b], "a_id": a, "b_id": b,
                "score": round(score.get(key, 0.0), 1),
                "games": d.get("games", 0), "playoff": d.get("playoff", 0),
                "close": d.get("close", 0), "prior": d.get("prior", 0)})
        proposed.sort(key=lambda x: -x["score"])

    grid = []
    for a in ids:
        row = {"name": names[a], "cells": []}
        for b in ids:
            if a == b:
                row["cells"].append(None)
            else:
                key = (a, b) if a < b else (b, a)
                row["cells"].append(round(score.get(key, 0.0), 1))
        grid.append(row)

    return templates.TemplateResponse(
        request=request, name="admin_rivals.html",
        context={"years": years, "season": season, "current": current,
                 "proposed": proposed, "total": total,
                 "owners": [{"owner_id": i, "username": names[i]} for i in ids],
                 "grid": grid, "headers": [names[i] for i in ids]})


@app.post("/admin/rivals/generate")
async def admin_rivals_generate(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    me = request.session.get("owner_id")
    form = await request.form()
    season = int(form["season"])

    with get_db() as conn:
        ids, names, score, _ = rivalry_scores(conn, season)
        pairs, total = best_pairing(ids, score)
        if not pairs:
            return RedirectResponse(
                url=f"/admin/rivals?season={season}&error=" +
                    quote("Need an even number of managers"), status_code=303)
        with conn.cursor() as cur:
            cur.execute("delete from rivalries where season_year = %s", (season,))
            for a, b in pairs:
                key = (a, b) if a < b else (b, a)
                s = round(score.get(key, 0.0), 2)
                cur.execute("""
                    insert into rivalries
                        (season_year, owner_id, rival_owner_id, score, source, created_by)
                    values (%s, %s, %s, %s, 'auto', %s), (%s, %s, %s, %s, 'auto', %s)
                """, (season, a, b, s, me, season, b, a, s, me))
        conn.commit()

    return RedirectResponse(
        url=f"/admin/rivals?season={season}&msg=" +
            quote(f"{len(pairs)} rivalries generated"), status_code=303)


@app.post("/admin/rivals/set")
async def admin_rivals_set(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    me = request.session.get("owner_id")
    form = await request.form()
    season = int(form["season"])

    picks = {}
    for key in form.keys():
        if key.startswith("rival_"):
            raw = (form.get(key) or "").strip()
            if raw:
                picks[int(key.split("_", 1)[1])] = int(raw)

    problems = []
    with get_db() as conn:
        ids, names, score, _ = rivalry_scores(conn, season)
        for a in ids:
            if a not in picks:
                problems.append(f"{names[a]} has no rival.")
        for a, b in picks.items():
            if a == b:
                problems.append(f"{names.get(a, a)} cannot rival themselves.")
            elif picks.get(b) != a:
                problems.append(
                    f"{names.get(a, a)} picks {names.get(b, b)}, "
                    f"but not the other way round.")

        if problems:
            return RedirectResponse(
                url=f"/admin/rivals?season={season}&error=" +
                    quote(" ".join(sorted(set(problems)))), status_code=303)

        with conn.cursor() as cur:
            cur.execute("delete from rivalries where season_year = %s", (season,))
            for a, b in picks.items():
                key = (a, b) if a < b else (b, a)
                cur.execute("""
                    insert into rivalries
                        (season_year, owner_id, rival_owner_id, score, source, created_by)
                    values (%s, %s, %s, %s, 'manual', %s)
                """, (season, a, b, round(score.get(key, 0.0), 2), me))
        conn.commit()

    return RedirectResponse(
        url=f"/admin/rivals?season={season}&msg=" + quote("Rivalries saved"),
        status_code=303)



import collections as _collections
import itertools as _itertools
import random as _random


def _pk(a, b):
    return (a, b) if a < b else (b, a)


def meeting_history(conn, season):
    """Regular-season meetings between each pair, all seasons before `season`."""
    rows = query(conn, """
        select g.owner_id, g.opponent_owner_id, count(*) as n
        from game_log g
        where g.season_year < %s and g.game_type = 'regular'
        group by g.owner_id, g.opponent_owner_id
    """, (season,))
    hist = {}
    for r in rows:
        k = _pk(r["owner_id"], r["opponent_owner_id"])
        hist[k] = max(hist.get(k, 0), r["n"])
    return hist


def _choose_doubles(ids, hist, rng, tries=4000):
    best = None
    for _ in range(tries):
        deg = {i: 0 for i in ids}
        chosen = []
        pairs = [_pk(a, b) for a, b in _itertools.combinations(ids, 2)]
        rng.shuffle(pairs)
        pairs.sort(key=lambda p: hist.get(p, 0) + rng.random() * 0.5)
        for p in pairs:
            a, b = p
            if deg[a] < 3 and deg[b] < 3:
                chosen.append(p)
                deg[a] += 1
                deg[b] += 1
        if all(v == 3 for v in deg.values()):
            cost = sum(hist.get(p, 0) for p in chosen)
            if best is None or cost < best[0]:
                best = (cost, chosen)
    return best[1] if best else None


def _one_matching(ids, pool, rng):
    adj = _collections.defaultdict(list)
    for p, c in pool.items():
        if c > 0:
            adj[p[0]].append(p[1])
            adj[p[1]].append(p[0])
    out = []

    def rec(remaining):
        if not remaining:
            return True
        remaining.sort(key=lambda x: len([y for y in adj[x] if y in remaining]))
        a = remaining[0]
        opts = [y for y in adj[a] if y in remaining]
        rng.shuffle(opts)
        for b in opts:
            out.append(_pk(a, b))
            rest = [x for x in remaining if x != a and x != b]
            if rec(rest):
                return True
            out.pop()
        return False

    return out if rec(list(ids)) else None



def generate_schedule(ids, hist, rival_pairs, seed, weeks=14, rival_week=10,
                      min_gap=2):
    """Weeks 1-11 a full round robin with rivalry week pinned, 12-14 the
    rematches, and no pair meeting within `min_gap` weeks."""
    rng = _random.Random(seed)
    doubles = _choose_doubles(ids, hist, rng)
    if not doubles:
        return None, "Could not build the doubled-opponent set."

    rp = [_pk(a, b) for a, b in rival_pairs]
    single = [_pk(a, b) for a, b in _itertools.combinations(ids, 2)]
    rr = len(ids) - 1

    for _ in range(800):
        pool = _collections.Counter(single)
        for p in rp:
            if pool[p] <= 0:
                return None, "Rivalry pairings are not valid matchups."
            pool[p] -= 1

        out = {rival_week: list(rp)}
        fail = False
        for w in range(1, rr + 1):
            if w == rival_week:
                continue
            m = _one_matching(ids, pool, rng)
            if m is None:
                fail = True
                break
            out[w] = m
            for p in m:
                pool[p] -= 1
        if fail or sum(pool.values()):
            continue

        dpool = _collections.Counter(doubles)
        ok = True
        for w in range(rr + 1, weeks + 1):
            m = _one_matching(ids, dpool, rng)
            if m is None:
                ok = False
                break
            out[w] = m
            for p in m:
                dpool[p] -= 1
        if not ok or sum(dpool.values()):
            continue

        when = _collections.defaultdict(list)
        for w, ms in out.items():
            for p in ms:
                when[p].append(w)
        if all(len(ws) < 2 or (max(ws) - min(ws)) >= min_gap
               for ws in when.values()):
            return out, None

    return None, ("Could not fit a schedule with no back-to-back rematches. "
                  "Generate again.")


@app.get("/admin/schedule", response_class=HTMLResponse)
def admin_schedule(request: Request, season: int = 0, seed: int = 0):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")

    with get_db() as conn:
        years = query(conn, "select season_year from seasons order by season_year desc")
        if not season:
            season = years[0]["season_year"]

        teams_list = query(conn, """
            select t.team_id, t.owner_id, t.team_name, o.username
            from teams t join owners o on o.owner_id = t.owner_id
            where t.season_year = %s order by o.username
        """, (season,))

        rivals = query(conn, """
            select owner_id, rival_owner_id from rivalries
            where season_year = %s and owner_id < rival_owner_id
        """, (season,))

        existing = query(conn, """
            select week, count(*) as n from matchups
            where season_year = %s and game_type = 'regular'
            group by week order by week
        """, (season,))

        hist = meeting_history(conn, season)

    ids = [t["owner_id"] for t in teams_list]
    names = {t["owner_id"]: t["username"] for t in teams_list}
    rival_pairs = [(r["owner_id"], r["rival_owner_id"]) for r in rivals]

    problem = None
    if not ids:
        problem = f"No teams entered for {season}."
    elif len(ids) % 2:
        problem = f"{len(ids)} managers. An even number is required."
    elif len(rival_pairs) * 2 != len(ids):
        problem = ("Rivalries are not set for this season. "
                   "Generate them on the Rivals page first.")

    weeks, err, counts = None, None, None
    if seed and not problem:
        sched, err = generate_schedule(ids, hist, rival_pairs, seed)
        if sched:
            weeks = []
            for w in sorted(sched):
                weeks.append({"week": w, "rival": w == 10,
                              "games": [(names[a], names[b]) for a, b in sched[w]]})
            pair_n = _collections.Counter()
            for w in sched:
                for p in sched[w]:
                    pair_n[p] += 1
            counts = []
            for p, c in sorted(pair_n.items(), key=lambda x: (-x[1], names[x[0][0]])):
                if c > 1:
                    counts.append({"a": names[p[0]], "b": names[p[1]],
                                   "times": c, "before": hist.get(p, 0)})

    return templates.TemplateResponse(
        request=request, name="admin_schedule.html",
        context={"years": years, "season": season, "seed": seed,
                 "weeks": weeks, "counts": counts, "error": err or problem,
                 "existing": existing, "teams": teams_list})


@app.post("/admin/schedule/save")
async def admin_schedule_save(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    seed = int(form["seed"])

    if not form.get("confirm"):
        return RedirectResponse(
            url=f"/admin/schedule?season={season}&seed={seed}&error=" +
                quote("Tick the confirm box first"), status_code=303)

    with get_db() as conn:
        teams_list = query(conn, """
            select owner_id, team_id from teams where season_year = %s
        """, (season,))
        by_owner = {t["owner_id"]: t["team_id"] for t in teams_list}
        rivals = query(conn, """
            select owner_id, rival_owner_id from rivalries
            where season_year = %s and owner_id < rival_owner_id
        """, (season,))
        hist = meeting_history(conn, season)

        ids = list(by_owner)
        pairs = [(r["owner_id"], r["rival_owner_id"]) for r in rivals]
        sched, err = generate_schedule(ids, hist, pairs, seed)
        if err:
            return RedirectResponse(
                url=f"/admin/schedule?season={season}&error=" + quote(err),
                status_code=303)

        rows = []
        for w, ms in sched.items():
            for a, b in ms:
                ta, tb = by_owner[a], by_owner[b]
                if ta > tb:
                    ta, tb = tb, ta
                rows.append((season, w, "regular", ta, tb))

        with conn.cursor() as cur:
            cur.execute("""
                delete from matchups
                where season_year = %s and game_type = 'regular'
                  and team_a_points is null
            """, (season,))
            cur.executemany("""
                insert into matchups
                    (season_year, week, game_type, team_a_id, team_b_id)
                values (%s, %s, %s, %s, %s)
                on conflict (season_year, week, team_a_id, team_b_id) do nothing
            """, rows)
        conn.commit()

    return RedirectResponse(
        url=f"/admin/schedule?season={season}&msg=" +
            quote(f"{len(rows)} matchups saved"), status_code=303)


@app.get("/health-check-tail")
def _tail_marker():
    return {"ok": True}



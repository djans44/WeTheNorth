import contextvars
import datetime
import decimal
import logging
import os
import pathlib
import threading
import time
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from app import crests as crestrules
from app import honours
from app import rosters as rosterrules
from app import standings
from app import summaries
from app import draft as draftboard
from app import transactions as txn
from app.keeperrules import next_free_round

load_dotenv()

# Uvicorn's logger, so anything reported here lands in the Render log beside
# the request that caused it.
log = logging.getLogger("uvicorn.error")

# The league runs on Eastern time and every keeper window closes at
# 11:59:59pm Eastern on its last day. Timestamps are stored as UTC, so a
# window closing at the end of Sep 10 is 03:59 UTC on Sep 11 -- printed raw it
# names the wrong day. Anything shown as a league date converts through here
# first. ZoneInfo, not a fixed -5, so the switch to daylight time is handled.
LEAGUE_TZ = ZoneInfo("America/Toronto")


def league_dates(opens, closes):
    """The span of a window as two league dates, e.g. "Sep 4 - Sep 11".

    No time of day: every window closes at the same hour, so printing it on
    each of three cards says nothing the page cannot say once. The day is the
    part that differs, and it is the part an owner is counting.
    """
    def day(ts):
        d = ts.astimezone(LEAGUE_TZ)
        # Not %-d: that is glibc only and would work on Render and crash on
        # the Windows machine this is developed on.
        return "%s %d" % (d.strftime("%b"), d.day)

    return "%s – %s" % (day(opens), day(closes))


def league_moment(s):
    """A wall-clock string from a datetime-local input, read as league time.

    The browser sends "2026-09-10T23:59" with no zone on it at all. Handed
    to Postgres as text it is cast using the session's timezone, which is
    GMT -- so an admin typing 11:59pm set the deadline to 7:59pm Eastern,
    four hours early, and nothing on any page said so. Attaching the zone
    here means the instant stored is the one that was typed.
    """
    return datetime.datetime.fromisoformat(s).replace(tzinfo=LEAGUE_TZ)


def league_field(ts):
    """A stored instant as the wall-clock string a datetime-local expects.

    The mirror of league_moment. Without it the form reads back the UTC
    hour, so an admin who saved 11:59pm correctly would reopen the page,
    see 3:59am, and "fix" it.
    """
    if not ts:
        return ""
    return ts.astimezone(LEAGUE_TZ).strftime("%Y-%m-%dT%H:%M")


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


def portrait_url(username):
    """The manager's painted portrait, or None if they have not been painted.

    Theo is retired and has none, so this has to answer honestly rather than
    return a path to a 404. Built by scripts/make_portraits.py from the
    originals in headshots/; the file is named for the username the URL uses.
    """
    if not username:
        return None
    name = "portraits/%s.webp" % username.lower()
    return static_url(name) if (STATIC_DIR / name).exists() else None


templates.env.globals["static_url"] = static_url
templates.env.globals["portrait_url"] = portrait_url
templates.env.filters["league_field"] = league_field


def league_day(ts):
    """A timestamp as the league reads it: its own date, in its own zone.

    Printed straight, a deadline set for the last night of December comes out
    as the first of January, because the column is stored in UTC and eleven
    at night in Toronto is four in the morning in London. league_window has
    said so since the keeper windows; this is the same thing for one date.
    """
    if not ts:
        return ""
    d = ts.astimezone(LEAGUE_TZ)
    return "%s %d" % (d.strftime("%b"), d.day)


templates.env.filters["league_day"] = league_day
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

    Playoff weeks count. Bane of Their Rival counts every meeting there has
    been, and some of those are playoff games.
    """
    return [(r["season_year"], r["week"]) for r in query(conn, """
        select distinct season_year, week from game_log
        order by season_year, week
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


def rings_from(titles):
    """One ring per manager, from crest rows already in sort order.

    setdefault keeps the same one the live ring would -- lowest sort_order
    first, and the page lists the rest. An empty result is meaningful: a
    season still being played has no snapshot to dress itself in, and the
    caller leaves the rings unset so today's are used.
    """
    rings = {}
    for c in titles:
        rings.setdefault(c["owner_id"], {"code": c["code"], "name": c["name"],
                                         "colour": c["colour"]})
    return rings


def season_rings(conn, year):
    """What each manager wore when that season closed."""
    return rings_from(query(conn, """
        select c.code, c.name, c.colour, o.owner_id
        from owner_crests oc
        join crests c on c.crest_id = oc.crest_id
        join owners o on o.owner_id = oc.owner_id
        where oc.season_year = %s and oc.week is null and c.standing = 'held'
        order by c.sort_order
    """, (year,)))


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


# Opening a connection to Neon costs about two and a half seconds from cold
# and far worse when several are asked for back to back -- seventeen seconds
# was measured opening them one after another. Running the queries costs tens
# of milliseconds: the page with the most work on the site spends 69ms on its
# largest query and 47ms on a trivial one. Every page took three seconds, and
# all but a few hundred milliseconds of that was dialling the database.
#
# So the connections are kept rather than made. Four is plenty for twelve
# people on one instance, and Neon counts them.
_pool = None
_pool_lock = threading.Lock()


def _db_pool():
    """Built on first use, not at import.

    Render's free tier cold-starts, and a pool that blocks startup would turn
    every wake into a failed boot rather than one slow request. The first
    request after a wake pays for the first connection, which is what it paid
    for every request before this.
    """
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                pool = ConnectionPool(
                    os.environ["DATABASE_URL"],
                    kwargs={"row_factory": dict_row},
                    min_size=1,
                    max_size=4,
                    # Neon drops idle connections and the instance itself
                    # sleeps, so a connection handed out after a quiet spell
                    # may be dead. check tests it first and replaces it,
                    # which is the difference between a slow page and a 500.
                    check=ConnectionPool.check_connection,
                    max_idle=120,
                    open=False,
                )
                pool.open()
                _pool = pool
    return _pool


def get_db():
    """A connection from the pool, returned to it on the way out.

    Same shape as the psycopg.connect() this replaces: used as a context
    manager it commits on a clean exit and rolls back on an exception. The
    only difference is that it goes back to the pool instead of being closed,
    so all fifty-odd callers are unchanged.
    """
    return _db_pool().connection()


@app.on_event("shutdown")
def _close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


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


def nav_unvoted(owner_id):
    """How many open assemblies this manager has not answered.

    A badge on the nav, so a vote that is open is a thing you are told about
    rather than a page you have to think to visit. One assembly runs at a
    time, so this is almost always nought or one -- it counts rather than
    assuming, because nothing stops two years being open at once.

    Not cached. It changes the moment someone votes, and a stale badge
    telling a manager to vote again after they have is worse than the query.
    """
    if not owner_id:
        return 0
    try:
        with get_db() as conn:
            return query(conn, """
                select count(*) as n from crest_polls p
                where p.closed_at is null and p.closes_at > now()
                  and not exists (
                      select 1 from crest_votes v
                      where v.poll_id = p.poll_id and v.owner_id = %s)
            """, (owner_id,))[0]["n"]
    except Exception:
        return 0


templates.env.globals["nav_owners"] = nav_owners
templates.env.globals["nav_seasons"] = nav_seasons
templates.env.globals["nav_unvoted"] = nav_unvoted


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


# The rounds that lead to the trophy. The consolation side is a separate
# ladder: losing a quarterfinal ends your championship whatever you win
# afterwards, so those rows are not part of being "still alive".
CHAMP_ROUNDS = ("quarterfinal", "semifinal", "championship")

# What being top of the pile is worth, said as the crest it is heading for
# rather than as a position. The league knows its crests, and "leads the
# league" says nothing a reader cannot already see in the table below.
# Warden of the North is the best regular-season record, which is what the
# table is ordered on; Crowned is the championship game.
#
# "in waiting" and "in line for" rather than "on course for": the last is a
# modern sports idiom sitting beneath a heading that is not. Both of these are
# the language of succession, which is the same thing a league table is.
WARDEN_PACE = "Warden of the North in waiting"
CROWN_PACE = "first in line for Crowned"


def still_alive(games, week):
    """Managers who can still win the championship going into `week`.

    Read off the bracket rows rather than worked out from a seed. A bye is a
    row with no opponent, so the two managers who sit out week 15 are present
    and are not mistaken for eliminated -- which is the trap in reading this
    from the fixtures alone. Anyone beaten on the championship side in an
    earlier week is dropped even if a later row still names them; nothing in
    the schedule does that today, but the set should not depend on it.
    """
    seen, lost = set(), set()
    for g in games:
        if g["game_type"] not in CHAMP_ROUNDS:
            continue
        if g["week"] >= week:
            seen.add(g["owner_a"])
            if g["owner_b"]:
                seen.add(g["owner_b"])
        elif g["points_a"] is not None and g["points_b"] is not None:
            lost.add(g["owner_b"] if g["points_a"] > g["points_b"]
                     else g["owner_a"])
    return seen - lost


def standings_after(conn, year, week):
    """The league table as it stood at the end of a given week.

    The query is in app/standings.py: a weekly summary is written from the
    same table, and two copies of it would drift.
    """
    return query(conn, standings.TABLE_AFTER_WEEK, {"y": year, "w": week})


@app.get("/season/{year}", response_class=HTMLResponse)
def season(request: Request, year: int, week: str | None = None):
    """A season, shown a week at a time.

    The selector at the top is the page's main control, and what it chooses
    decides the whole shape rather than only which scores are on screen:

      season    the year as a whole -- the table, the honour roll, the
                records, the keepers. Where a finished season opens.
      preview   the first week. Keeps the season preview above it forever,
                because the preview is about that week whether or not it has
                since been played.
      regular   any other week before the playoffs.
      playoffs  a week with bracket rows in it. No league table: by then the
                table has stopped deciding anything.

    Each of the three week states leads with a face and prose -- the read of
    the week just gone, beside whoever the week belongs to.
    """
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

        # The prose, where any has been written. Newest per target wins: the
        # table is appended to rather than updated, so a regenerated summary
        # is a new row and the one it replaced stays as the record of what
        # was there. distinct on is why the index carries generated_at desc.
        week_summaries = {r["week"]: r["body"] for r in query(conn, """
            select distinct on (week) week, body
            from summaries
            where season_year = %s and kind = 'week'
              and published_at is not null
            order by week, published_at desc
        """, (year,))}
        # The recap, and only the recap. The year as a whole once took
        # whichever of the preview or the recap was published later, which
        # put a finished season's own preview in the place its recap goes --
        # a page that reads as though nobody ever wrote the recap. The
        # preview is about the year ahead and belongs to week 1, where the
        # preview state fetches it for itself.
        rows = query(conn, """
            select body from summaries
            where season_year = %s and kind = 'season'
              and published_at is not null
            order by published_at desc limit 1
        """, (year,))
        season_summary = rows[0] if rows else None

        weeks = []
        for g in games:
            if not weeks or weeks[-1]["week"] != g["week"]:
                weeks.append({"week": g["week"], "games": [], "played": False})
            g["rival"] = (g["owner_a"], g["owner_b"]) in rival_pairs
            weeks[-1]["games"].append(g)
            if g["points_a"] is not None:
                weeks[-1]["played"] = True

        # Rivalry week is the week where every game is a rival meeting, which
        # is read off the fixtures rather than pinned to a number. The
        # schedule generator puts it in week 10, but a week that merely
        # happens to be numbered 10 is not rivalry week: the pre-2026
        # schedules came from Yahoo and know nothing about rivalries. Across
        # all five seasons this is true of exactly one week -- 2026's tenth --
        # and of no other. A lone rival meeting elsewhere, of which there are
        # several, does not qualify.
        for w in weeks:
            w["rivalry"] = len(w["games"]) > 1 and all(g["rival"] for g in w["games"])

        # Which weeks are playoff weeks comes from the rows, not from 15-17:
        # a season whose bracket has not been drawn yet has none, and its
        # last week is an ordinary one.
        numbers = [w["week"] for w in weeks]
        playoff_weeks = {g["week"] for g in games if g["game_type"] != "regular"}
        played = [w["week"] for w in weeks if w["played"]]
        finished = bool(head[0]["champion"])

        # What the selector is pointing at. `week=season` is the year as a
        # whole; anything unrecognised falls back to the default rather than
        # answering 404, because a stale link should still land somewhere.
        want = (week or "").strip().lower()
        if want.isdigit() and int(want) in numbers:
            shown = int(want)
        elif want == "season" or finished or not numbers:
            # A finished season opens on itself: the last week of the year is
            # the week just gone, and the year is the more interesting read.
            shown = None
        else:
            # Mid-season, open on what just happened rather than on week 1.
            shown = played[-1] if played else numbers[0]

        if shown is None:
            state = "season"
        elif shown in playoff_weeks:
            state = "playoffs"
        elif shown == numbers[0]:
            state = "preview"
        else:
            state = "regular"

        this_week = next((w for w in weeks if w["week"] == shown), None)

        # The league table under a week is that week's table, not the
        # season's: on a finished year the season-wide one would show the
        # final order underneath week three. Seed marks go with it -- they
        # describe a race that is still being run, and are computed from the
        # season-wide table, so they belong only to the year as a whole.
        if state in ("preview", "regular"):
            standings = standings_after(conn, year, shown)
            seeds = {}
        else:
            seeds = playoff_labels(standings, remaining)

        def champion_of(y):
            """The champion of a season, and the title they wore at its close.

            The held-title snapshot for a season is taken after it ends, so
            the champion of y is already wearing Protector of the Realm in
            y's own rows -- which is why the caption can read as a title
            rather than as "champion of y". Lowest sort_order wins, the same
            rule the sigil rings use, so a face and the ring beside it can
            never name two different titles.
            """
            rows = query(conn, """
                select s.username, s.team_name, t.name as title
                from team_season_stats s
                left join lateral (
                    select c.name, c.sort_order
                    from owner_crests oc
                    join crests c on c.crest_id = oc.crest_id
                    where oc.owner_id = s.owner_id
                      and oc.season_year = s.season_year
                      and oc.week is null and c.standing = 'held'
                    order by c.sort_order limit 1
                ) t on true
                where s.season_year = %s and s.final_rank = 1
            """, (y,))
            return rows[0] if rows else None

        # The banner: a face, and the season's or the week's prose beside it.
        #
        # The section is drawn whether or not anything has been written yet.
        # A page whose shape depends on whether a summary exists is a page the
        # league has to learn twice, and the place the writing goes should be
        # visible before there is any writing in it. Standing in for the prose
        # is a line saying what will appear there and when.
        #
        # Who is pictured follows the prose. A preview belongs to a season
        # that has not started, so the face is last year's champion -- the one
        # with something to defend. A recap belongs to a season that has
        # finished, so it is this year's. A week belongs to whoever is winning
        # the thing that week is part of: the league table during the regular
        # season, the bracket once the bracket is what matters.
        #
        # Week N carries week N-1's read, because a week's summary is written
        # from its scores and cannot exist until the week is over. That leaves
        # the first week with no week to look back on, which is exactly what
        # the season preview is for.
        def leader(rows):
            """Top of a table, or nobody if nobody has played yet.

            Ordered by a record everyone shares at 0-0, the first row is
            whichever team the sort happened to leave there. That is not a
            leader, and it would put a face on the page that has earned
            nothing.
            """
            if not rows:
                return None
            top = rows[0]
            games_played = top["wins"] + top["losses"] + (top["ties"] or 0)
            return top if games_played else None

        if state == "season":
            # The face is whoever is on top of the thing this page is about:
            # the champion once there is one, the table while there is not,
            # and the defending champion before a ball has been thrown.
            if finished:
                who = champion_of(year)
                aside = who["title"] if who else None
            elif played:
                who, aside = leader(standings), WARDEN_PACE
            else:
                who = champion_of(year - 1)
                aside = who["title"] if who else None
            banner = {
                # The annalistic form -- thus begins, thus passed, thus
                # ended -- which is how a chronicle marks its divisions. The
                # first attempt at this used "at the close" and "the year
                # ahead": short noun phrases, correct English, and period
                # neutral. The site's own flavoured headings lean archaic
                # (Mightiest weeks, Cruellest defeats, Longest winter) and
                # these did not.
                # A year still being played has not ended, and saying it has
                # on every in-progress season page is a small lie the heading
                # tells before the prose gets a word in.
                "title": "Thus ended the year" if finished else
                         "Thus stands the year",
                "body": season_summary["body"] if season_summary else None,
                # A finished season says the account is missing; one still
                # being played says when it arrives. "Once the last game is
                # played" would be a lie on every season that ended years ago.
                "waiting": "Nothing has yet been set down of the year."
                           if finished else
                           "The year is not yet ended, and so not yet told.",
                "username": who["username"] if who else None,
                "aside": aside if who else None,
            }
        elif state == "preview":
            rows = query(conn, """
                select body from summaries
                where season_year = %s and kind = 'preview'
                  and published_at is not null
                order by published_at desc limit 1
            """, (year,))
            who = champion_of(year - 1)
            banner = {
                "title": "Thus begins the year",
                "aside": who["title"] if who else None,
                "body": rows[0]["body"] if rows else None,
                "waiting": "Nothing has yet been set down of the year to come.",
                "username": who["username"] if who else None,
            }
        else:
            if state == "regular":
                # First in the table drawn underneath, so the face and the
                # rows below it agree with each other.
                who, aside = leader(standings), WARDEN_PACE
            else:
                # The best regular-season finisher of those still in it. The
                # regular table is the seeding, so it is read at the last week
                # before the bracket rather than from the season as a whole.
                last_regular = max((n for n in numbers if n not in playoff_weeks),
                                   default=None)
                alive = still_alive(games, shown)
                who, aside = None, CROWN_PACE
                if last_regular and alive:
                    who = next((r for r in standings_after(conn, year, last_regular)
                                if r["username"] in alive), None)
            banner = {
                "title": "Thus passed week %d" % (shown - 1),
                "body": week_summaries.get(shown - 1),
                "waiting": "Nothing has yet been set down of week %d."
                           % (shown - 1),
                "username": who["username"] if who else None,
                "aside": aside if who else None,
            }

        brackets = playoff_brackets(games)
        titles, crest_groups = season_roll(crest_rows)

    # Every sigil on the page wears the title its manager held when this
    # season closed. `titles` is already in sort_order, so setdefault keeps
    # the same one the live ring would -- lowest first, page lists the rest.
    #
    # A season still being played has no snapshot to dress itself in, and the
    # empty map is left unset rather than passed: today's rings are the right
    # answer for today's season.
    rings = rings_from(titles)
    token = _page_rings.set(rings) if rings else None
    try:
        return templates.TemplateResponse(
            request=request, name="season.html",
            context={"s": head[0], "standings": standings, "weeks": weeks,
                     "state": state, "shown": shown, "this_week": this_week,
                     "banner": banner, "records": records,
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
               (resolved_at is null and now() between opens_at and closes_at) as is_open,
               -- A window whose clock has run out and that nobody has
               -- resolved yet. It is neither open nor settled, and the page
               -- used to call it "upcoming", which is the one thing it
               -- certainly is not. A phase is closed when an admin resolves
               -- it, not when the clock passes, so this state can last days.
               (resolved_at is null and now() > closes_at) as is_lapsed
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
        # Which year of the term a contract-filled round is in, out of how
        # many, the way the results page says it. The card used to say only
        # "contract", which is the least interesting half: a contract running
        # out next year and one with two still to go are different facts.
        term = None
        if c:
            held = by_id.get(c["player_id"]) or {}
            yrs, signed = held.get("contract_years"), held.get("signed_season")
            if yrs and signed and 1 <= season - signed + 1 <= yrs:
                term = "%d/%d" % (season - signed + 1, yrs)

        phases.append({
            "n": n, "opens_at": w["opens_at"], "closes_at": w["closes_at"],
            "dates": league_dates(w["opens_at"], w["closes_at"]),
            "contract_term": term, "contract_until": (by_id.get(c["player_id"])
                                                      or {}).get("final_season")
                                                     if c else None,
            "resolved_at": w["resolved_at"], "is_open": w["is_open"],
            "is_lapsed": w["is_lapsed"],
            "contract": by_id.get(c["player_id"]) if c else None,
            "submission": subs.get(n),
            "planned_id": p["player_id"] if p else None,
            "planned_term": p["term_years"] if p else None,
        })

    has_plans = any(p["player_id"] for p in plans.values())
    choices = [e for e in elig if e["state"] in ("free", "must_sign")]

    # Where selection stands, for the line at the top. Worked out here rather
    # than in four branches of Jinja.
    #
    # The round you are in is the earliest one nobody has resolved, not
    # whichever window the clock has open. The two come apart whenever the
    # commissioner is slow: round one's window shuts, round two's opens the
    # same minute, and until round one is resolved the league is still in it.
    # Naming the open window made the page say Round 2 while Round 1 was
    # unsettled, which is a different and wrong story.
    lead = "%d selection" % season
    current = next((p for p in phases if not p["resolved_at"]), None)
    live = next((p for p in phases if p["is_open"]), None)

    if not phases:
        standing = {"kind": "none", "word": "No windows",
                    "sub": "%s. No windows have been set." % lead}
    elif current is None:
        standing = {"kind": "set", "word": "Settled",
                    "sub": "%s. All %d rounds resolved." % (lead, len(phases))}
    else:
        word = "Keeper Round %d" % current["n"]
        if current["is_open"]:
            standing = {"kind": "live", "word": word,
                        "sub": "%s. Open until %s."
                               % (lead, current["closes_at"].strftime("%b %d"))}
        elif current["is_lapsed"]:
            # Another window is usually open by now, and saying so is the
            # actionable half: the round you are in is waiting on someone
            # else, the one after it is waiting on you.
            after = (" Round %d is open until %s."
                     % (live["n"], live["closes_at"].strftime("%b %d"))
                     if live and live["n"] != current["n"] else
                     " A plan saved before they do still counts.")
            standing = {"kind": "live", "word": word,
                        "sub": "%s. Closed, and with the commissioner.%s"
                               % (lead, after)}
        else:
            standing = {"kind": "none", "word": word,
                        "sub": "%s. Opens %s."
                               % (lead, current["opens_at"].strftime("%b %d"))}

    return {
        "windows": windows, "phases": phases, "standing": standing,
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


def keeper_grid(conn, season, finished, phases):
    """Who was kept, by manager and by the phase they were kept in.

    Two sources, because a finished season and the one being chosen are
    different kinds of fact.

    **The season being chosen** knows its phases exactly. Contracts reserve
    the earliest ones automatically -- keeper_phase_plan is that rule -- and
    an approved submission is the settled answer for its phase, so it lands
    on top of whatever the plan reserved there.

    **A finished season does not record a phase at all.** keeper_selections
    says who was kept and at what round, and nothing about the order it
    happened in. So the phases are reconstructed by applying the same rule
    the live page uses: contracts first, longest remaining, then cheapest
    round, then name; free choices after them in round order. That is an
    inference and the page says so rather than passing it off as a record.
    """
    grid = {}

    def term(r):
        """Which year of a contract this is, out of how many.

        A one-year contract reads 1/1 and a three-year one 1/3 through 3/3,
        which is the whole of what there is: the rules allow those two
        lengths and nothing else. A keeper in their first year has no
        contract yet and gets neither number.
        """
        years, year = r.get("contract_years"), r.get("contract_year")
        on = r.get("contract_id") is not None
        fits = bool(years and year and 1 <= year <= years)
        return {"contract": on, "inferred": finished,
                "year": year if fits else None,
                "years": years if fits else None}

    def put(oid, phase, cell):
        if phase and phase <= phases:
            grid.setdefault(oid, {})[phase] = cell

    if finished:
        rows = query(conn, """
            select t.owner_id, ks.cost_round, ks.keeper_year, p.full_name,
                   ks.contract_id, kc.contract_years,
                   ks.season_year - kc.signed_season + 1 as contract_year,
                   kc.signed_season + kc.contract_years - 1 as final_season
            from keeper_selections ks
            join teams t on t.team_id = ks.team_id
                        and t.season_year = ks.season_year
            join players p on p.player_id = ks.player_id
            left join keeper_contracts kc on kc.contract_id = ks.contract_id
            where ks.season_year = %s
        """, (season,))
        by_owner = {}
        for r in rows:
            by_owner.setdefault(r["owner_id"], []).append(r)
        for oid, mine in by_owner.items():
            mine.sort(key=lambda r: (
                r["contract_id"] is None,
                -((r["final_season"] or season) - season + 1),
                r["cost_round"], r["full_name"]))
            for i, r in enumerate(mine, start=1):
                put(oid, i, dict(term(r), name=r["full_name"],
                                 round=r["cost_round"], settled=True))
    else:
        for r in query(conn, """
            select k.owner_id, k.phase, k.cost_round, k.contract_id,
                   p.full_name, kc.contract_years,
                   k.season_year - kc.signed_season + 1 as contract_year
            from keeper_phase_plan k
            join players p on p.player_id = k.player_id
            join keeper_contracts kc on kc.contract_id = k.contract_id
            where k.season_year = %s
        """, (season,)):
            put(r["owner_id"], r["phase"],
                dict(term(r), name=r["full_name"], round=r["cost_round"],
                     settled=False))
        for r in query(conn, """
            select s.owner_id, s.phase, s.cost_round, s.contract_id,
                   p.full_name, kc.contract_years,
                   s.season_year - kc.signed_season + 1 as contract_year
            from keeper_submissions s
            join players p on p.player_id = s.player_id
            left join keeper_contracts kc on kc.contract_id = s.contract_id
            where s.season_year = %s and s.status = 'approved'
              and s.player_id is not null
        """, (season,)):
            put(r["owner_id"], r["phase"],
                dict(term(r), name=r["full_name"], round=r["cost_round"],
                     settled=True))
    return grid


@app.get("/keepers/results", response_class=HTMLResponse)
def keeper_results(request: Request, season: int = 0):
    """What settled, as opposed to the choosing of it.

    /keepers is where the three phases are worked through, and it is about
    what you are about to do. This is about what was done.
    """
    with get_db() as conn:
        years = query(conn, "select season_year from seasons order by season_year desc")
        if not season:
            season = years[0]["season_year"]

        size = query(conn, """
            select keeper_count, is_complete from seasons where season_year = %s
        """, (season,))
        phases = (size[0]["keeper_count"] if size else 3) or 0
        finished = bool(size and size[0]["is_complete"])

        owners = query(conn, """
            select distinct o.owner_id, o.username
            from teams t join owners o on o.owner_id = t.owner_id
            where t.season_year = %s order by o.username
        """, (season,))

        grid = keeper_grid(conn, season, finished, phases)

        # Which windows an admin has closed. A phase nobody has resolved is
        # still open, and a cell in it is a reservation rather than a result.
        done = {r["phase"] for r in query(conn, """
            select phase from keeper_windows
            where season_year = %s and resolved_at is not null
        """, (season,))}

        rings = season_rings(conn, season)

    kept = sum(1 for m in grid.values() for c in m.values() if c["settled"])
    token = _page_rings.set(rings) if rings else None
    try:
        return templates.TemplateResponse(
            request=request, name="keeper_results.html",
            context={"years": years, "season": season, "owners": owners,
                     "grid": grid, "phases": list(range(1, phases + 1)),
                     "resolved": done, "finished": finished, "kept": kept})
    finally:
        if token is not None:
            _page_rings.reset(token)


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


def score_value(raw, where, errors):
    """One points or projection box, checked before it reaches the column.

    These went to the insert as whatever was typed, so a stray letter, a minus
    sign or a very long number came back as a 500 with an error page and
    everything else the admin had entered gone. A team entered twice has always
    produced a tidy message and kept the form; numbers get the same now.
    """
    if raw is None:
        return None
    try:
        n = decimal.Decimal(raw)
    except decimal.InvalidOperation:
        errors.append("%s is not a number: %s" % (where, raw))
        return None
    if not n.is_finite():
        errors.append("%s is not a number: %s" % (where, raw))
        return None
    if n < 0:
        errors.append("%s cannot be less than nothing: %s" % (where, raw))
        return None
    if n >= 1000:
        errors.append("%s is too large to be a score: %s" % (where, raw))
        return None
    return str(n)


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
                 mode: str = "", crests: str = "", msg: str = ""):
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
        # What has been written about this week, so approving it happens
        # where the scores that produced it were entered rather than on a
        # page the admin has to remember to visit.
        account = week_summary_state(conn, season, week) if week else None
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
                 "mixed": len(set(LAYOUTS[mode])) > 1,
                 "filled": filled, "crests": crests,
                 "account": account, "errors": [], "losing": 0,
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

    rows, seen, number_errors = [], [], []
    for i, gt in enumerate(layout):
        a, b = val(f"r{i}_team_a"), val(f"r{i}_team_b")
        where = "Row %d" % (i + 1)
        row = {"game_type": gt,
               "team_a_id": int(a) if a else None,
               "team_b_id": int(b) if b else None,
               "pa": score_value(val(f"r{i}_pa"), where + " points", number_errors),
               "pb": score_value(val(f"r{i}_pb"), where + " opponent points", number_errors),
               "ja": score_value(val(f"r{i}_ja"), where + " projection", number_errors),
               "jb": score_value(val(f"r{i}_jb"), where + " opponent projection", number_errors)}
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
        # What is in the week now, to work out what saving would take out of
        # it, and to put the account back on the page if this bounces.
        before = query(conn, """
            select matchup_id, game_type, team_a_id, team_b_id, team_a_points
            from matchups where season_year = %s and week = %s
        """, (season, week))
        account = week_summary_state(conn, season, week) if week else None
        # A summary pinned to one of these matchups would make the delete
        # fail on the foreign key, which is a 500 rather than a sentence.
        # Nothing writes those today; this is here so that stays true.
        pinned = query(conn, """
            select count(*) as n from summaries
            where matchup_id in (
                select matchup_id from matchups
                where season_year = %s and week = %s)
        """, (season, week))[0]["n"] if before else 0

    names = {t["team_id"]: t["team_name"] for t in teams_list}
    errors = list(number_errors)
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
    if pinned:
        errors.append("An account is pinned to a matchup in this week, so the "
                      "week cannot be rewritten. Remove it first.")

    # What saving takes away. The week is deleted and rewritten, so anything
    # in it that is not in the form goes -- and it used to go quietly. Picking
    # the wrong Round was enough: the layout comes back empty because the game
    # types do not match, the empty rows are skipped, and the delete had
    # already run. Two clicks emptied a played week and the page said "Saved 0
    # matchups" in green.
    keeping = {(r["game_type"], r["team_a_id"], r["team_b_id"])
               for r in rows if r["team_a_id"]}
    losing = [r for r in before
              if (r["game_type"], r["team_a_id"], r["team_b_id"]) not in keeping]
    scored = [r for r in losing if r["team_a_points"] is not None]
    if before and not keeping:
        errors.append("Every row is blank, so this would empty week %d rather "
                      "than save it. Check the Round is right for this week."
                      % week)
    elif losing and not form.get("confirm"):
        errors.append(
            "This removes %d matchup%s from week %d%s. Tick the box below if "
            "that is meant." % (len(losing), "" if len(losing) == 1 else "s",
                                week,
                                ", %d with scores in" % len(scored) if scored else ""))

    if errors:
        return templates.TemplateResponse(
            request=request, name="admin_scores.html", status_code=400,
            context={"years": years, "season": season, "week": week, "mode": mode,
                     "requested": mode, "week_modes": WEEK_MODES,
                     "teams": teams_list, "rows": rows, "filled": filled,
                     "mixed": len(set(LAYOUTS[mode])) > 1,
                     "account": account, "errors": errors,
                     "losing": len(losing), "labels": MODE_LABELS})

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

        # A week of scores changes who holds nine titles, how far every
        # streak has run and half the weekly crests, so it is recomputed
        # here rather than left for someone to remember a command. From this
        # week on only: week nine cannot change who held a title in week
        # eight, and the whole season takes fifteen seconds where this takes
        # about two.
        #
        # A failure here must not lose the scores, which are already
        # committed. It is reported instead, and the hand-run script fixes
        # it: python scripts/award_crests.py --season <year> --apply
        try:
            _, wrote = crestrules.recompute(conn, season, week)
            crested = str(wrote)
        except Exception:
            log.exception("crest recompute failed for %s week %s", season, week)
            crested = "failed"

    # The week's account, if this is the save that completed the week. Runs
    # off the request thread and is allowed to fail quietly -- see
    # queue_week_summary. Nothing here can lose the scores, which are
    # committed above.
    started = False
    try:
        started = queue_week_summary(season, week)
    except Exception:
        log.exception("could not start the week %s summary for %s", week, season)

    # Through the toast the rest of the app uses, rather than the green
    # paragraph this page kept above the form. A failed crest recompute is the
    # exception: it wants doing something about, so it goes to the page as an
    # error rather than sliding away after four seconds.
    from urllib.parse import quote
    told = "Saved %d matchup%s" % (len(prepared), "" if len(prepared) == 1 else "s")
    if losing:
        told += ", %d removed" % len(losing)
    url = (f"/admin/scores?season={season}&week={week}&mode={mode}"
           f"&msg={quote(told + '.')}")
    if crested == "failed":
        url += "&crests=failed"
    return RedirectResponse(url + ("#account" if started else ""), status_code=303)


# ---------------------------------------------------------------- transactions

def transaction_context(conn, season, text="", trades="", previewed=False):
    """Everything the page shows, for a paste or for an empty box.

    The parse and the matching are pure -- app/transactions.py -- so this is
    the three lookups they need and nothing else.
    """
    years = query(conn, "select season_year from seasons order by season_year desc")
    teams = {txn.norm(r["team_name"]): r["team_id"] for r in query(conn, """
        select team_id, team_name from teams where season_year = %s
    """, (season,))}
    players = {}
    for r in query(conn, "select player_id, full_name from players"):
        players.setdefault(txn.norm(r["full_name"]), r["player_id"])
    stored = query(conn, """
        select count(*) as n, max(occurred_on) as newest
        from transactions where season_year = %s
    """, (season,))[0]
    existing = {(r["season_year"], r["kind"], r["player_id"], r["to_team_id"],
                 r["from_team_id"], r["occurred_raw"]) for r in query(conn, """
        select season_year, kind, player_id, to_team_id, from_team_id,
               occurred_raw
        from transactions where season_year = %s
    """, (season,))}

    ctx = {"years": years, "season": season, "text": text, "trades": trades,
           "previewed": previewed, "stored": stored["n"],
           "newest_stored": stored["newest"], "ready": [], "known": [],
           "unresolved": [], "puzzles": [], "wanted": [], "flow": None}
    if not text.strip() and not trades.strip():
        return ctx

    # Two boxes, each parsed as the thing it is meant to hold. A block of the
    # wrong shape is reported rather than reinterpreted, which is the point of
    # keeping them apart.
    moves, puzzles = [], []
    for source, expect in ((text, "adds"), (trades, "trades")):
        if source.strip():
            got, odd = txn.parse(source, season, expect)
            moves += got
            puzzles += odd
    ready, known, unresolved, wanted = txn.resolve(
        moves, teams, players, existing, season)
    ctx.update({"ready": ready, "known": known, "unresolved": unresolved,
                "puzzles": puzzles,
                "wanted": sorted(wanted.values()),
                "flow": txn.continuity(ready, known, stored["newest"])})
    return ctx


@app.get("/admin/transactions", response_class=HTMLResponse)
def admin_transactions(request: Request, season: int = 0, msg: str = "",
                       error: str = ""):
    """Paste what Yahoo shows; the page works out what is new.

    Yahoo pages its transaction log at twenty-five, so pasting a whole season
    late on is a chore nobody would keep up. Pasting the visible page every
    week is not, and the overlap between one paste and the next is what makes
    it safe: a row already stored is recognised and left alone.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    with get_db() as conn:
        years = query(conn, "select season_year from seasons order by season_year desc")
        if not season:
            season = years[0]["season_year"] if years else 0
        ctx = transaction_context(conn, season)
    return templates.TemplateResponse(request=request,
                                      name="admin_transactions.html", context=ctx)


@app.post("/admin/transactions")
async def admin_transactions_post(request: Request):
    """Look at it, then write it. Never one without the other.

    Preview re-reads the paste rather than carrying a parsed payload, because
    parsing is deterministic: the same text gives the same rows, and what is
    applied is what was on the screen.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    text = form.get("text") or ""
    trades = form.get("trades") or ""
    apply_it = form.get("action") == "apply"

    with get_db() as conn:
        ctx = transaction_context(conn, season, text, trades, previewed=True)

        if not apply_it:
            return templates.TemplateResponse(
                request=request, name="admin_transactions.html", context=ctx)

        # The four things that stop a write, each of which the preview has
        # already shown. They are separate so the page can say which one.
        stop = ""
        if not ctx["ready"]:
            stop = "Nothing new in that paste."
        elif ctx["unresolved"]:
            stop = ("%d move%s names a team the league does not have. Fix the "
                    "team name first." % (len(ctx["unresolved"]),
                                          "" if len(ctx["unresolved"]) == 1 else "s"))
        elif ctx["wanted"] and not form.get("make_players"):
            stop = "Tick the box to create the players that are new."
        elif ctx["puzzles"] and not form.get("skip_puzzles"):
            stop = "Tick the box to go ahead without the blocks that made no sense."
        elif ctx["flow"] and ctx["flow"]["state"] == "gap" and not form.get("accept_gap"):
            stop = ("There is a gap between this paste and what is stored. "
                    "Scroll back a page and paste more, or tick the box.")
        if stop:
            ctx["stop"] = stop
            return templates.TemplateResponse(
                request=request, name="admin_transactions.html",
                context=ctx, status_code=400)

        made = 0
        with conn.cursor() as cur:
            # New players first: the rows about to be written point at them.
            if ctx["wanted"]:
                for name, position in ctx["wanted"]:
                    cur.execute("""
                        insert into players (full_name, position)
                        values (%s, %s) returning player_id
                    """, (name, position))
                    made += 1
                players = {}
                for r in query(conn, "select player_id, full_name from players"):
                    players.setdefault(txn.norm(r["full_name"]), r["player_id"])
                for row in ctx["ready"]:
                    if row["player_id"] is None:
                        row["player_id"] = players.get(txn.norm(row["player"]))

            # on conflict: the unique index is the real guard, and two
            # people pasting the same page at once should be a no-op rather
            # than an error page.
            cur.executemany("""
                insert into transactions
                    (season_year, kind, method, faab_amount, player_id,
                     to_team_id, from_team_id, occurred_on, occurred_raw)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
            """, [(season, r["kind"], r["method"], r["faab"], r["player_id"],
                   r["to_team_id"], r["from_team_id"], r["on"], r["date"])
                  for r in ctx["ready"]])
        conn.commit()

        # The Sellsword and Traffic in Men count a season's moves, and Master
        # of Coin and Master of Whisperers count every season's, so a load of
        # transactions is exactly the thing that can change who holds them.
        # Entering a week's scores has recomputed crests since it was built;
        # this is the same recompute for the same reason.
        #
        # Every completed season from this one on, not just this one: the two
        # career titles are cumulative, so adding a trade to 2022 moves what
        # the count stood at when 2023 and 2024 closed as well.
        #
        # A failure here must not lose the transactions, which are committed
        # above. It is reported instead, and scripts/award_crests.py --apply
        # is the fix.
        crested = ""
        try:
            crestrules.recompute_market(conn, [r["season_year"] for r in query(
                conn, "select season_year from seasons order by season_year")])
        except Exception:
            log.exception("crest recompute failed after loading %s transactions",
                          season)
            crested = " The crests could not be recomputed."

    told = "%d transaction%s stored" % (len(ctx["ready"]),
                                        "" if len(ctx["ready"]) == 1 else "s")
    if made:
        told += ", %d new player%s created" % (made, "" if made == 1 else "s")
    return RedirectResponse(
        url="/admin/transactions?season=%d&msg=%s"
            % (season, quote(told + "." + crested)),
        status_code=303)


# ----------------------------------------------------------------- draft board

def draft_context(conn, season, text="", previewed=False, positions=None):
    """The board as pasted, checked against the season it claims to be."""
    years = query(conn, "select season_year from seasons order by season_year desc")
    teams = {draftboard.norm(r["team_name"]): r["team_id"] for r in query(conn, """
        select team_id, team_name from teams where season_year = %s
    """, (season,))}
    players = {}
    for r in query(conn, "select player_id, full_name from players"):
        players.setdefault(draftboard.norm(r["full_name"]), r["player_id"])
    stored = query(conn, """
        select count(*) as n, count(*) filter (where is_keeper) as kept,
               max(round) as rounds
        from draft_picks where season_year = %s
    """, (season,))[0]

    ctx = {"years": years, "season": season, "text": text,
           "previewed": previewed, "stored": stored["n"],
           "stored_kept": stored["kept"], "stored_rounds": stored["rounds"],
           "picks": [], "puzzles": [], "problems": [], "wanted": [],
           "rounds": [], "positions": positions or {},
           "choices": draftboard.POSITIONS}
    if not text.strip():
        return ctx

    picks, puzzles = draftboard.parse(text)
    problems = draftboard.check(picks, teams,
                                {"year": season, "count": len(teams)})
    rows, wanted = draftboard.resolve(picks, teams, players)
    # Laid out round by round, the way a board is read.
    by_round = {}
    for r in rows:
        by_round.setdefault(r["round"], []).append(r)
    ctx.update({"picks": rows, "puzzles": puzzles, "problems": problems,
                "wanted": wanted,
                "rounds": [(n, sorted(by_round[n], key=lambda p: p["pick"]))
                           for n in sorted(by_round)]})
    return ctx


POSITION_ORDER = {p: i for i, p in
                  enumerate(("QB", "RB", "WR", "TE", "K", "DEF"))}


# --------------------------------------------------------------- the vote

def poll_for(conn, season=None):
    """The poll for a season, or the one that is open if no season is named."""
    if season:
        rows = query(conn, """
            select * from crest_polls where season_year = %s
        """, (season,))
    else:
        rows = query(conn, """
            select * from crest_polls
            order by (closed_at is null and closes_at > now()) desc,
                     season_year desc limit 1
        """)
    return rows[0] if rows else None


def poll_is_open(poll):
    """Open until it is closed or its date passes. One rule, asked in one
    place, so the page and the post can never disagree about it."""
    if not poll or poll["closed_at"]:
        return False
    return poll["closes_at"] > datetime.datetime.now(datetime.timezone.utc)


def poll_result(conn, poll):
    """Every honour's count, most votes first, and whether it ties.

    The tie is reported rather than resolved: awarding is still the manual
    grant, and a tie is exactly the thing a commissioner is for.
    """
    rows = query(conn, """
        select v.crest_id, v.candidate, v.detail, v.choice_owner_id,
               count(*) as votes, o.username
        from crest_votes v
        join owners o on o.owner_id = v.choice_owner_id
        where v.poll_id = %s
        group by v.crest_id, v.candidate, v.detail, v.choice_owner_id, o.username
        order by count(*) desc, o.username
    """, (poll["poll_id"],))
    out = {}
    for r in rows:
        out.setdefault(r["crest_id"], []).append(r)
    for crest_id, tally in out.items():
        top = tally[0]["votes"]
        for r in tally:
            r["winner"] = r["votes"] == top
        # A tie only matters at the top.
        out[crest_id] = {"tally": tally,
                         "tied": sum(1 for r in tally if r["votes"] == top) > 1}
    return out


@app.get("/assembly", response_class=HTMLResponse)
def assembly_page(request: Request, season: int = 0, msg: str = "", error: str = ""):
    """The four honours nobody can earn, put to the twelve people who hold
    opinions about them.

    One ballot, four questions, one vote each. The ballots are built from the
    season rather than nominated, because the candidates are already known:
    the twelve team names, the twelve managers, each side of each trade, and
    every defeat by ten points or fewer.
    """
    me = request.session.get("owner_id")
    with get_db() as conn:
        poll = poll_for(conn, season or None)
        if not poll:
            return templates.TemplateResponse(
                request=request, name="assembly.html",
                context={"poll": None, "open": False, "ballots": {},
                         "crests": [], "mine": {}, "result": {},
                         "turnout": 0, "voters": 0})

        crests = query(conn, """
            select crest_id, code, name, description from crests
            where active and award_mode = 'manual' order by sort_order
        """)
        def q(sql, p=()):
            return query(conn, sql, p)
        ballots = honours.ballots(q, poll["season_year"])
        mine = {r["crest_id"]: r["candidate"] for r in query(conn, """
            select crest_id, candidate from crest_votes
            where poll_id = %s and owner_id = %s
        """, (poll["poll_id"], me or 0))}
        voters = query(conn, """
            select count(distinct owner_id) as n from crest_votes
            where poll_id = %s
        """, (poll["poll_id"],))[0]["n"]
        seats = query(conn, """
            select count(*) as n from teams where season_year = %s
        """, (poll["season_year"],))[0]["n"]

        live = poll_is_open(poll)

    return templates.TemplateResponse(
        request=request, name="assembly.html",
        context={"poll": poll, "open": live, "ballots": ballots,
                 "crests": crests, "mine": mine,
                 "voters": voters, "seats": seats,
                 "msg": msg, "error": error})


@app.post("/assembly")
async def assembly_vote(request: Request):
    """One vote each per honour, changeable until the poll closes."""
    me = request.session.get("owner_id")
    if not me:
        raise HTTPException(status_code=403, detail="Sign in to vote")
    from urllib.parse import quote
    form = await request.form()

    with get_db() as conn:
        poll = poll_for(conn, int(form.get("season") or 0) or None)
        if not poll_is_open(poll):
            return RedirectResponse(
                url="/assembly?error=" + quote("That vote is closed."),
                status_code=303)

        def q(sql, p=()):
            return query(conn, sql, p)
        ballots = honours.ballots(q, poll["season_year"])
        crests = query(conn, """
            select crest_id, code from crests
            where active and award_mode = 'manual'
        """)

        cast = 0
        with conn.cursor() as cur:
            for c in crests:
                picked = form.get("crest_%d" % c["crest_id"])
                if not picked:
                    continue
                # The candidate has to be on the ballot it claims to be on.
                # The form came through a browser, and a vote for something
                # nobody could see would be a crest granted out of nowhere.
                found = next((b for b in ballots.get(c["code"], [])
                              if b["candidate"] == picked), None)
                if not found:
                    continue
                cur.execute("""
                    insert into crest_votes
                        (poll_id, crest_id, owner_id, choice_owner_id,
                         candidate, detail)
                    values (%s, %s, %s, %s, %s, %s)
                    on conflict (poll_id, crest_id, owner_id) do update
                       set choice_owner_id = excluded.choice_owner_id,
                           candidate = excluded.candidate,
                           detail = excluded.detail,
                           cast_at = now()
                """, (poll["poll_id"], c["crest_id"], me,
                      found["owner_id"], found["candidate"], found["detail"]))
                cast += 1
        conn.commit()

    told = "%d vote%s recorded. Change them any time before it closes." % (
        cast, "" if cast == 1 else "s")
    return RedirectResponse(url="/assembly?msg=" + quote(told), status_code=303)


@app.get("/assembly/proclamations", response_class=HTMLResponse)
def proclamations_page(request: Request, season: int = 0):
    """What the assembly decided, once it has decided it.

    Counts only. Who voted for what is nobody's business but theirs -- the
    point of a secret ballot is that a manager can say the truth about a
    friend's team name.
    """
    with get_db() as conn:
        years = [r["season_year"] for r in query(conn, """
            select season_year from crest_polls
            where closed_at is not null order by season_year desc
        """)]
        if not years:
            return templates.TemplateResponse(
                request=request, name="proclamations.html",
                context={"years": [], "season": 0, "poll": None,
                         "crests": [], "result": {}, "held": {}, "voters": 0})
        if season not in years:
            season = years[0]
        poll = poll_for(conn, season)
        crests = query(conn, """
            select crest_id, code, name, description from crests
            where active and award_mode = 'manual' order by sort_order
        """)
        result = poll_result(conn, poll)
        voters = query(conn, """
            select count(distinct owner_id) as n from crest_votes
            where poll_id = %s
        """, (poll["poll_id"],))[0]["n"]
        # What was actually granted, which is the vote made real. Until the
        # commissioner confirms, the count is a result and not yet an honour.
        held = {r["crest_id"]: r for r in query(conn, """
            select oc.crest_id, oc.detail, o.username
            from owner_crests oc join owners o on o.owner_id = oc.owner_id
            where oc.season_year = %s
        """, (season,))}

    return templates.TemplateResponse(
        request=request, name="proclamations.html",
        context={"years": years, "season": season, "poll": poll,
                 "crests": crests, "result": result, "held": held,
                 "voters": voters})


@app.get("/rosters", response_class=HTMLResponse)
def rosters_page(request: Request, season: int = 0, who: str = ""):
    """Who holds whom, worked out where it can be and stored where it cannot.

    From 2026 a roster is the draft with every move since applied to it. The
    seasons before that were loaded as adds with no drops, so applying them
    would only ever add -- those show the end-of-season snapshot instead, and
    the page says which of the two it is looking at.
    """
    with get_db() as conn:
        years = [r["season_year"] for r in query(conn, """
            select season_year from seasons order by season_year desc
        """)]
        if season not in years:
            season = years[0] if years else 0

        snapshot = query(conn, """
            select r.team_id, r.player_id, r.acquired,
                   p.full_name, p.position
            from rosters r join players p on p.player_id = r.player_id
            where r.season_year = %s
        """, (season,))
        picks = query(conn, """
            select d.team_id, d.player_id, d.round, d.is_keeper,
                   p.full_name, p.position
            from draft_picks d
            left join players p on p.player_id = d.player_id
            where d.season_year = %s order by d.round, d.pick_in_round
        """, (season,))
        moves = query(conn, """
            select x.kind, x.player_id, x.to_team_id, x.occurred_on,
                   p.full_name, p.position
            from transactions x join players p on p.player_id = x.player_id
            where x.season_year = %s
            order by x.occurred_on, x.transaction_id
        """, (season,))
        sides = query(conn, """
            select t.team_id, t.team_name, o.username
            from teams t join owners o on o.owner_id = t.owner_id
            where t.season_year = %s order by o.username
        """, (season,))

    # The snapshot is the record where there is one. Deriving needs a draft
    # and moves of its own, and without either there is nothing honest to show.
    if snapshot:
        source = "snapshot"
        squads = {}
        for r in snapshot:
            squads.setdefault(r["team_id"], []).append(
                {"player_id": r["player_id"], "full_name": r["full_name"],
                 "position": r["position"], "how": r["acquired"] or "",
                 "round": None, "when": None})
    elif picks and moves:
        source = "derived"
        squads = rosterrules.derive(picks, moves)
    else:
        source = "none"
        squads = {}

    for team in squads.values():
        team.sort(key=lambda r: (POSITION_ORDER.get(r["position"], 9),
                                 r["full_name"] or ""))

    # One squad at a time, or all twelve. Naming one narrows the page rather
    # than changing it: the same rosters, read at a size meant for reading
    # instead of for comparing.
    one = next((s for s in sides if s["username"].lower() == who.lower()), None)
    if who and not one:
        who = ""

    return templates.TemplateResponse(
        request=request, name="rosters.html",
        context={"years": years, "season": season, "source": source,
                 "sides": sides, "squads": squads, "who": who, "one": one,
                 "moves": len(moves), "picks": len(picks)})


@app.get("/transactions", response_class=HTMLResponse)
def transactions_page(request: Request, season: int = 0, who: str = "",
                     kind: str = ""):
    """Every move of a season, newest first.

    An add and the drop that made room for it arrive as two rows sharing a
    moment and a manager, which is how Yahoo prints them and how they are
    argued about afterwards -- "he cut Doubs for that" is one event, not two.
    So they are put back together here rather than listed apart.
    """
    with get_db() as conn:
        years = [r["season_year"] for r in query(conn, """
            select distinct season_year from transactions order by season_year desc
        """)]
        if not years:
            return templates.TemplateResponse(
                request=request, name="transactions.html",
                context={"years": [], "season": 0, "moves": [], "owners": [],
                         "who": "", "tally": {}})
        if season not in years:
            season = years[0]

        rows = query(conn, """
            select x.kind, x.method, x.faab_amount, x.occurred_on, x.occurred_raw,
                   p.full_name, p.position,
                   ot.username as to_who, ofr.username as from_who
            from transactions x
            join players p on p.player_id = x.player_id
            left join teams tt on tt.team_id = x.to_team_id
            left join owners ot on ot.owner_id = tt.owner_id
            left join teams ft on ft.team_id = x.from_team_id
            left join owners ofr on ofr.owner_id = ft.owner_id
            where x.season_year = %s
            order by x.occurred_on desc, x.transaction_id desc
        """, (season,))
        owners = [r["username"] for r in query(conn, """
            select distinct o.username
            from transactions x
            join teams t on t.team_id in (x.to_team_id, x.from_team_id)
            join owners o on o.owner_id = t.owner_id
            where x.season_year = %s order by o.username
        """, (season,))]

    # An add, and the drop that shares its moment and its manager, are one
    # move. Keyed on the raw string Yahoo printed, which carries the minute.
    moves, index = [], {}
    for r in rows:
        whose = r["to_who"] or r["from_who"]
        key = (r["occurred_raw"], whose, r["kind"] == "trade")
        if key not in index:
            index[key] = {"when": r["occurred_on"], "raw": r["occurred_raw"],
                          "who": whose, "trade": r["kind"] == "trade",
                          "in": [], "out": []}
            moves.append(index[key])
        side = "out" if r["kind"] == "drop" else "in"
        index[key][side].append(r)

    def touches(move, name):
        """A trade is filed under whoever received the player, so filtering on
        the manager a move is filed under would hide the half of every trade
        they gave away."""
        low = name.lower()
        if move["who"] and move["who"].lower() == low:
            return True
        return any(low in {(r["to_who"] or "").lower(),
                           (r["from_who"] or "").lower()}
                   for r in move["in"] + move["out"])

    if who:
        moves = [m for m in moves if touches(m, who)]
    # A trade and a waiver claim are different kinds of afternoon, and looking
    # for one through the other is the whole reason to filter. Everything that
    # is not a trade is one bucket: a free agent and a waiver claim differ
    # only in whether anyone else wanted him.
    if kind == "trade":
        moves = [m for m in moves if m["trade"]]
    elif kind == "add":
        moves = [m for m in moves if not m["trade"]]

    tally = {"add": 0, "drop": 0, "trade": 0}
    for r in rows:
        if who and who.lower() not in {(r["to_who"] or "").lower(),
                                       (r["from_who"] or "").lower()}:
            continue
        if kind == "trade" and r["kind"] != "trade":
            continue
        if kind == "add" and r["kind"] == "trade":
            continue
        tally[r["kind"]] = tally.get(r["kind"], 0) + 1
    # Whether the season has drops at all, which is a different question from
    # whether the manager being looked at has any. 2025 was loaded before
    # drops were recorded and has only the few pasted since.
    season_drops = sum(1 for r in rows if r["kind"] == "drop")

    return templates.TemplateResponse(
        request=request, name="transactions.html",
        context={"years": years, "season": season, "moves": moves,
                 "owners": owners, "who": who, "kind": kind, "tally": tally,
                 "season_drops": season_drops,
                 "any_trades": any(r["kind"] == "trade" for r in rows)})


@app.get("/draft-results", response_class=HTMLResponse)
def draft_results(request: Request, season: int = 0):
    """What the draft came to, round by round.

    Draft order is who picks when, draft prep is what to do about it, and this
    is the third thing: what actually happened. It reads draft_picks, which is
    also the source of every keeper cost basis, so a season showing nothing
    here is a season whose keeper prices cannot be worked out.
    """
    with get_db() as conn:
        years = [r["season_year"] for r in query(conn, """
            select distinct season_year from draft_picks order by season_year desc
        """)]
        if not years:
            return templates.TemplateResponse(
                request=request, name="draft_results.html",
                context={"years": [], "season": 0, "rounds": [], "teams": [],
                         "kept": 0})
        if season not in years:
            season = years[0]
        rows = query(conn, """
            select d.round, d.pick_in_round, d.is_keeper,
                   p.full_name, p.position,
                   t.team_name, o.username, o.owner_id
            from draft_picks d
            join teams t on t.team_id = d.team_id
            join owners o on o.owner_id = t.owner_id
            left join players p on p.player_id = d.player_id
            where d.season_year = %s
            order by d.round, d.pick_in_round
        """, (season,))

    by_round, by_team = {}, {}
    for r in rows:
        by_round.setdefault(r["round"], []).append(r)
        by_team.setdefault((r["owner_id"], r["team_name"], r["username"]),
                           []).append(r)
    return templates.TemplateResponse(
        request=request, name="draft_results.html",
        context={"years": years, "season": season,
                 "rounds": [(n, by_round[n]) for n in sorted(by_round)],
                 "teams": sorted(((k, v) for k, v in by_team.items()),
                                 key=lambda kv: kv[0][2]),
                 "kept": sum(1 for r in rows if r["is_keeper"])})


@app.get("/admin/draft", response_class=HTMLResponse)
def admin_draft(request: Request, season: int = 0, msg: str = "", error: str = ""):
    """Paste the board, see what it says, then save it."""
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    with get_db() as conn:
        years = query(conn, "select season_year from seasons order by season_year desc")
        if not season:
            season = years[0]["season_year"] if years else 0
        ctx = draft_context(conn, season)
    return templates.TemplateResponse(request=request, name="admin_draft.html",
                                      context=ctx)


@app.post("/admin/draft")
async def admin_draft_post(request: Request):
    """Check it, then write it. A draft is a whole season at once.

    Unlike transactions, which arrive a week at a time and accumulate, a board
    is one object: it is saved entire or not at all, and saving replaces the
    season's picks rather than adding to them.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    text = form.get("text") or ""
    save_it = form.get("action") == "save"
    # A position for each player the league has never seen, chosen on the
    # review screen because the board does not carry one.
    positions = {k[4:]: v for k, v in form.items()
                 if k.startswith("pos_") and v}

    with get_db() as conn:
        ctx = draft_context(conn, season, text, previewed=True,
                            positions=positions)
        if not save_it:
            return templates.TemplateResponse(
                request=request, name="admin_draft.html", context=ctx)

        stop = ""
        # Only the ones the board did not name a position for. The three-line
        # shape carries one, so most boards ask nothing.
        missing = [w for w in ctx["wanted"] if not w["position"]
                   and positions.get(w["key"]) not in draftboard.POSITIONS]
        if ctx["problems"]:
            stop = "The board does not check out. Nothing has been written."
        elif ctx["puzzles"]:
            stop = "Some lines could not be read. Nothing has been written."
        elif missing:
            stop = ("Choose a position for %d player%s the league has never seen."
                    % (len(missing), "" if len(missing) == 1 else "s"))
        elif ctx["stored"] and not form.get("replace"):
            stop = ("%d already has a draft of %d picks. Tick the box to replace it."
                    % (season, ctx["stored"]))
        if stop:
            ctx["stop"] = stop
            return templates.TemplateResponse(
                request=request, name="admin_draft.html", context=ctx,
                status_code=400)

        made = 0
        with conn.cursor() as cur:
            for w in ctx["wanted"]:
                cur.execute("""
                    insert into players (full_name, position) values (%s, %s)
                """, (w["name"], w["position"] or positions[w["key"]]))
                made += 1
            if made:
                players = {}
                for r in query(conn, "select player_id, full_name from players"):
                    players.setdefault(draftboard.norm(r["full_name"]), r["player_id"])
                for p in ctx["picks"]:
                    if p["player_id"] is None:
                        p["player_id"] = players.get(draftboard.norm(p["player"]))

            # The whole board at once: the slot and player keys are unique per
            # season, so a partial rewrite would collide with itself.
            cur.execute("delete from draft_picks where season_year = %s", (season,))
            cur.executemany("""
                insert into draft_picks
                    (season_year, round, pick_in_round, team_id, player_id,
                     is_keeper)
                values (%s, %s, %s, %s, %s, %s)
            """, [(season, p["round"], p["pick"], p["team_id"], p["player_id"],
                   p["keeper"]) for p in ctx["picks"]])
        conn.commit()

    told = "%d picks saved" % len(ctx["picks"])
    if made:
        told += ", %d new player%s created" % (made, "" if made == 1 else "s")
    return RedirectResponse(
        url="/admin/draft?season=%d&msg=%s" % (season, quote(told + ".")),
        status_code=303)


@app.get("/admin/crests", response_class=HTMLResponse)
def admin_crests(request: Request, season: int = 0):
    """The four crests nobody can earn.

    Best team name, conduct at the draft, the trade of the year, the defeat
    nobody deserved. They are opinions, so no rule computes them and the
    commissioner writes them down. They have existed since the catalogue was
    seeded and there has never been a way to give one.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    with get_db() as conn:
        years = query(conn, "select season_year from seasons order by season_year desc")
        season = season or (years[0]["season_year"] if years else 0)
        manual = query(conn, """
            select crest_id, code, name, description, category
            from crests where active and award_mode = 'manual'
            order by sort_order
        """)
        owners = query(conn, """
            select owner_id, username from owners
            where not is_retired order by username
        """)
        # Every grant ever made, not just this season's: four a year is a
        # short enough list to show whole, and seeing last year's is how you
        # remember what you called it.
        given = query(conn, """
            select oc.owner_crest_id, oc.season_year, oc.detail,
                   c.name as crest, c.crest_id, c.code, c.category,
                   o.owner_id, o.username,
                   b.username as by_whom
            from owner_crests oc
            join crests c on c.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            left join owners b on b.owner_id = oc.awarded_by
            where c.award_mode = 'manual'
            order by oc.season_year desc, c.sort_order
        """)
        poll = poll_for(conn, season)
        vote = {"poll": poll, "open": poll_is_open(poll), "result": {},
                "voters": 0}
        if poll:
            vote["voters"] = query(conn, """
                select count(distinct owner_id) as n from crest_votes
                where poll_id = %s
            """, (poll["poll_id"],))[0]["n"]
            if not vote["open"]:
                vote["result"] = poll_result(conn, poll)
    return templates.TemplateResponse(
        request=request, name="admin_crests.html",
        context={"vote": vote,"years": years, "season": season, "manual": manual,
                 "owners": owners, "given": given})


@app.post("/admin/crests")
async def admin_crests_grant(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    crest_id = int(form["crest_id"])
    owner_id = int(form["owner_id"])
    detail = (form.get("detail") or "").strip() or None

    with get_db() as conn:
        with conn.cursor() as cur:
            # One holder per crest per season: the finest name of 2025 is one
            # name. Granting again replaces rather than adding, so a change of
            # mind does not need a delete first.
            cur.execute("""
                delete from owner_crests oc using crests c
                 where c.crest_id = oc.crest_id and c.award_mode = 'manual'
                   and oc.crest_id = %s and oc.season_year = %s
            """, (crest_id, season))
            cur.execute("""
                insert into owner_crests
                    (owner_id, crest_id, season_year, detail, awarded_by)
                values (%s, %s, %s, %s, %s)
            """, (owner_id, crest_id, season, detail,
                  request.session.get("owner_id")))
            name = query(conn, "select name from crests where crest_id = %s",
                         (crest_id,))[0]["name"]
            who = query(conn, "select username from owners where owner_id = %s",
                        (owner_id,))[0]["username"]
        conn.commit()
    return RedirectResponse(
        url=f"/admin/crests?season={season}&msg=" +
            quote(f"{name} awarded to {who} for {season}."),
        status_code=303)


@app.post("/admin/crests/poll")
async def admin_crests_poll(request: Request):
    """Open the league's vote on a season, or close it and see the count.

    Closing is what reveals the result -- to everyone at once, the
    commissioner included. Awarding is still the grant above: a poll decides
    who should hold a crest and the commissioner writes it down, which is
    what leaves a tie something to be settled by.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    doing = form.get("action")

    with get_db() as conn:
        with conn.cursor() as cur:
            if doing == "open":
                closes = (form.get("closes") or "").strip()
                if not closes:
                    return RedirectResponse(
                        url="/admin/crests?season=%d&error=%s"
                            % (season, quote("Give the vote a closing date.")),
                        status_code=303)
                cur.execute("""
                    insert into crest_polls (season_year, opened_by, closes_at)
                    values (%s, %s, %s)
                    on conflict (season_year) do update
                       set closes_at = excluded.closes_at, closed_at = null
                """, (season, request.session.get("owner_id"),
                      league_moment(closes)))
                told = "The %d vote is open." % season
            elif doing == "close":
                cur.execute("""
                    update crest_polls set closed_at = now()
                    where season_year = %s and closed_at is null
                """, (season,))
                told = ("The %d vote is closed and the count is on the page."
                        % season)
            else:
                told = ""
        conn.commit()
    return RedirectResponse(
        url="/admin/crests?season=%d&msg=%s" % (season, quote(told)),
        status_code=303)


@app.post("/admin/crests/proclaim")
async def admin_crests_proclaim(request: Request):
    """Turn a closed assembly's count into the four crests it decided.

    One press rather than four, because the vote already said who each one
    belongs to. It grants exactly what the grant form grants -- same table,
    same replace-rather-than-add, same awarded_by -- so an honour proclaimed
    this way is indistinguishable afterwards from one written by hand, which
    is the point: the poll decides, the commissioner still gives.

    A tie is left alone and named. Two candidates level is the one thing a
    count cannot settle, and settling it is what a commissioner is for.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])

    with get_db() as conn:
        poll = poll_for(conn, season)
        if not poll or not poll["closed_at"]:
            return RedirectResponse(
                url="/admin/crests?season=%d&error=%s"
                    % (season, quote("That assembly has not risen yet.")),
                status_code=303)

        result = poll_result(conn, poll)
        given, tied = 0, []
        with conn.cursor() as cur:
            for crest_id, r in result.items():
                if r["tied"]:
                    tied.append(crest_id)
                    continue
                win = r["tally"][0]
                cur.execute("""
                    delete from owner_crests oc using crests c
                     where c.crest_id = oc.crest_id and c.award_mode = 'manual'
                       and oc.crest_id = %s and oc.season_year = %s
                """, (crest_id, season))
                cur.execute("""
                    insert into owner_crests
                        (owner_id, crest_id, season_year, detail, awarded_by)
                    values (%s, %s, %s, %s, %s)
                """, (win["choice_owner_id"], crest_id, season, win["detail"],
                      request.session.get("owner_id")))
                given += 1
        conn.commit()

        names = [r["name"] for r in query(conn, """
            select name from crests where crest_id = any(%s) order by sort_order
        """, (tied,))] if tied else []

    told = "%d honour%s proclaimed" % (given, "" if given == 1 else "s")
    if names:
        told += ", and %s left tied for you to settle" % _listed_names(names)
    return RedirectResponse(
        url="/admin/crests?season=%d&msg=%s" % (season, quote(told + ".")),
        status_code=303)


def _listed_names(names):
    if len(names) == 1:
        return names[0]
    return "%s and %s" % (", ".join(names[:-1]), names[-1])


@app.post("/admin/crests/revoke")
async def admin_crests_revoke(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    row = int(form["owner_crest_id"])
    season = int(form["season"])
    with get_db() as conn:
        # award_mode in the delete as well as the id: a stray id from
        # somewhere else must not be able to remove a computed crest through
        # this route.
        with conn.cursor() as cur:
            cur.execute("""
                delete from owner_crests oc using crests c
                 where c.crest_id = oc.crest_id and c.award_mode = 'manual'
                   and oc.owner_crest_id = %s
            """, (row,))
            gone = cur.rowcount
        conn.commit()
    return RedirectResponse(
        url=f"/admin/crests?season={season}&msg=" +
            quote("Taken back." if gone else "Nothing to take back."),
        status_code=303)


@app.get("/health")
def health():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1")
            cur.fetchone()
    return {"status": "ok", "database": "connected"}




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
            rd = next_free_round(c["cost_round"], taken.get(oid, set()))
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
            rd = next_free_round(e["cost_round"], taken.get(oid, set()))
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
                # Read as league time, not as whatever the database session
                # happens to be set to. See league_moment.
                opens = league_moment(opens)
                closes = league_moment(closes)
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


def refresh_keeper_crests(conn, season):
    """Two crests are made of keeper data: Oathbreaker counts voids and Three
    Oaths Sworn counts contracts held at once. Both were only ever recomputed
    by score entry, which has nothing to do with either -- so cancelling a
    void in the offseason left the badge standing until somebody entered a
    week, which for a season that has not started is months.

    Called from every route that writes keeper_voids or keeper_selections.
    Swallowed the way the score-entry call is: a crest is a decoration, and
    failing to redraw one must never fail the keeper action that prompted it.
    """
    try:
        crestrules.recompute(conn, season)
    except Exception:
        log.exception("keeper crest refresh failed for %s", season)


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
        if not err:
            # Selections have just become real, so Three Oaths Sworn may have
            # changed for whoever a contract landed on.
            refresh_keeper_crests(conn, season)

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
                err = "No such keeper round."
            elif win["resolved_at"]:
                err = f"Keeper Round {phase} has already closed."
            elif not win["is_open"] and not is_admin:
                err = f"The Keeper Round {phase} window is not open."

            if not err:
                cur.execute("""
                    select 1 from keeper_submissions
                    where season_year = %s and phase = %s and owner_id = %s
                """, (season, phase, target))
                if cur.fetchone():
                    err = f"Keeper Round {phase} is already submitted."

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
                    err = (f"Keeper Round {phase} is filled by a contract. "
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
                    err = f"Choose a player for Keeper Round {phase} first."

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
                rd = next_free_round(want, taken)
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
            else "&msg=" + quote(f"Keeper Round {phase} submitted and locked"))
    return RedirectResponse(url=url, status_code=303)



@app.post("/keepers/forfeit")
async def forfeit_phase(request: Request):
    """Give up one round on purpose, rather than by missing it.

    A forfeit already existed, but only as something resolution did to you
    when a window shut with nothing in it. There was no way to say "I am not
    keeping anyone this round" and have it recorded, which an owner with one
    player worth keeping and three rounds to fill wants to do.

    **It settles one round and nothing else.** Rounds are filled
    independently -- contracts take the earliest ones, free choices fill what
    is left -- so giving up round two leaves round three exactly as it was.
    The same guards as a submission, because it is one: a row with no player
    against it, auto-approved the way resolution's own forfeits are.
    """
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
            cur.execute("""
                select resolved_at, (now() between opens_at and closes_at) as is_open
                from keeper_windows where season_year = %s and phase = %s
            """, (season, phase))
            win = cur.fetchone()
            if not win:
                err = "No such round."
            elif win["resolved_at"]:
                err = f"Keeper Round {phase} has already closed."
            elif not win["is_open"] and not is_admin:
                err = f"The Keeper Round {phase} window is not open."

            if not err:
                cur.execute("""
                    select 1 from keeper_submissions
                    where season_year = %s and phase = %s and owner_id = %s
                """, (season, phase, target))
                if cur.fetchone():
                    err = f"Keeper Round {phase} is already submitted."

            if not err:
                cur.execute("""
                    select player_id from keeper_phase_plan
                    where season_year = %s and owner_id = %s and phase = %s
                """, (season, target, phase))
                if cur.fetchone():
                    err = (f"Keeper Round {phase} is filled by a contract. "
                           "A contract is an obligation; void it instead.")

            if not err and phase == 1:
                cur.execute("""
                    select count(*) as n from keeper_voids
                    where season_year = %s and owner_id = %s and confirmed_at is null
                """, (season, target))
                if cur.fetchone()["n"]:
                    err = ("Submit or clear your void first. Voiding changes "
                           "which rounds are available.")

            if not err:
                cur.execute("""
                    insert into keeper_submissions
                        (season_year, phase, owner_id, origin, status, note)
                    values (%s, %s, %s, 'forfeit', 'approved', %s)
                """, (season, phase, target, "forfeited deliberately"))
                # The plan for this round is spent. The others are untouched:
                # forfeiting one round is not forfeiting the rest.
                cur.execute("""
                    delete from keeper_plans
                    where season_year = %s and phase = %s and owner_id = %s
                """, (season, phase, target))
        conn.commit()

    url = f"/keepers?season={season}&owner={target}"
    url += ("&error=" + quote(err) if err
            else "&msg=" + quote(f"Keeper Round {phase} forfeited"))
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

        # The same as the draft prep and season pages: a sigil here wears
        # what its manager held when the season being looked at closed, not
        # what they hold today.
        rings = season_rings(conn, season)

    for r in rows:
        r["finish"] = finishes.get(r["owner_id"])
        r["ballots"] = weights.get(r["owner_id"])
        r["is_new"] = r["owner_id"] in newcomers
    ballot_total = sum(weights.values())

    taken = {r["slot"]: r["username"] for r in rows if r["slot"]}
    on_clock = next((r for r in rows if r["is_on_the_clock"]), None)
    my_turn = bool(on_clock and on_clock["owner_id"] == me)

    # Who follows, so the room knows who to look at next. The next unfilled
    # row rather than simply the next row, because an admin can set a slot
    # out of order and that row is already done.
    on_deck = None
    if on_clock:
        at = next(i for i, r in enumerate(rows) if r["is_on_the_clock"])
        on_deck = next((r for r in rows[at + 1:] if not r["slot"]), None)

    token = _page_rings.set(rings) if rings else None
    try:
        return templates.TemplateResponse(
            request=request, name="draft_order.html",
            context={"years": years, "season": season, "rows": rows,
                     "slots": list(range(1, team_count + 1)), "taken": taken,
                     "on_clock": on_clock, "on_deck": on_deck,
                     "my_turn": my_turn, "chosen": len(taken),
                     "entrants": len(rows), "ballot_total": ballot_total,
                     "prev_season": season - 1, "is_admin": is_admin,
                     "me": me, "msg": msg, "error": error})
    finally:
        if token is not None:
            _page_rings.reset(token)


@app.post("/draft-order/pick")
async def draft_order_pick(request: Request):
    from urllib.parse import quote
    me = request.session.get("owner_id")
    is_admin = bool(request.session.get("is_admin"))
    form = await request.form()
    season = int(form["season"])
    target = int(form.get("owner_id") or me)

    # The radio carries required, so an empty confirm is refused by the
    # browser. A post that arrives without one came from somewhere else.
    slot = int(form["slot"]) if form.get("slot") else 0
    if not slot:
        return RedirectResponse(
            url=f"/draft-order?season={season}&error=" + quote("Choose a slot first"),
            status_code=303)

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

    with get_db() as conn:
        # Drawing over an existing order was its own quiet way to lose one.
        # Removing and then drawing is the same thing in two visible steps,
        # and the page only offers whichever of the two applies.
        drawn = query(conn, "select count(*) as n from draft_order "
                            "where season_year = %s", (season,))[0]["n"]
        if drawn:
            return RedirectResponse(
                url=f"/draft-order?season={season}&error=" +
                    quote(f"{season} already has a lottery. Remove it first."),
                status_code=303)

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


@app.post("/admin/draft-order/clear")
async def admin_draft_clear(request: Request):
    """Remove the lottery entirely, back to a season that has not drawn one.

    Distinct from drawing again, which replaces the order and keeps twelve
    rows. This deletes them, which is what you want when the lottery was run
    for the wrong season or run too early.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])

    with get_db() as conn:
        # Counted before the delete, so the message can say what was actually
        # lost rather than implying slots went with it when none had been
        # chosen.
        had = query(conn, """
            select count(*) as entrants, count(slot) as picked
            from draft_order where season_year = %s
        """, (season,))[0]
        with conn.cursor() as cur:
            cur.execute("delete from draft_order where season_year = %s",
                        (season,))
        conn.commit()

    if not had["entrants"]:
        msg = f"No lottery to remove for {season}."
    elif had["picked"]:
        msg = (f"Lottery removed for {season}. {had['entrants']} entrants, "
               f"{had['picked']} of whom had chosen a slot.")
    else:
        msg = (f"Lottery removed for {season}. {had['entrants']} entrants, "
               f"none of whom had chosen yet.")
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
    # No is_admin here. Nothing on this page is gated: what it shows is the
    # league's own record, and what it lets you change is your own session.
    with get_db() as conn:
        years = query(conn, "select season_year from seasons order by season_year desc")
        if not season:
            season = years[0]["season_year"]

        # Everyone in the lottery, whether or not they have taken a slot.
        # Filtering on a slot here was the page's central fault: a manager
        # who had not chosen yet vanished from both grids, keepers and all,
        # and keepers have nothing to do with choosing a draft slot.
        entrants = query(conn, """
            select d.owner_id, o.username, d.slot, d.lottery_position
            from draft_order d join owners o on o.owner_id = d.owner_id
            where d.season_year = %s
            order by d.lottery_position
        """, (season,))

        size = query(conn, """
            select team_count, keeper_count, is_complete
            from seasons where season_year = %s
        """, (season,))
        team_count = size[0]["team_count"] if size else 12
        # The league's own number rather than a 3 written in here. 2022 ran
        # with no keepers at all and the column says so.
        keeper_count = (size[0]["keeper_count"] if size else 3) or 0
        # A finished season is a record, not a plan. Its keepers live in
        # keeper_selections, written when the year closed. keeper_eligibility
        # only ever holds the season being prepped and keeper_submissions is
        # the queue for it, which is why reading those for 2023 drew a board
        # with nothing on it.
        finished = bool(size and size[0]["is_complete"])

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

        if finished:
            real = query(conn, """
                select t.owner_id, ks.cost_round, ks.player_id, ks.keeper_year,
                       p.full_name, p.position, null::smallint as term_years
                from keeper_selections ks
                join teams t on t.team_id = ks.team_id
                            and t.season_year = ks.season_year
                join players p on p.player_id = ks.player_id
                where ks.season_year = %s
            """, (season,))
        else:
            real = query(conn, """
                select s.owner_id, s.cost_round, s.term_years, s.player_id,
                       p.full_name, p.position, null::smallint as keeper_year
                from keeper_submissions s
                join players p on p.player_id = s.player_id
                where s.season_year = %s and s.status = 'approved'
                  and s.player_id is not null
            """, (season,))

        # Every sigil on the page wears what its manager held when this
        # season closed, the way the season page does. Without it a board
        # from 2023 came out wearing today's crowns, which is a different
        # story about a draft that happened three years ago. A season still
        # being played returns nothing here and keeps today's rings, which
        # is the right answer for it.
        rings = season_rings(conn, season)

        # Placeholders live in the reader's own session, not the database.
        # They are a what-if -- "say Chris keeps Mahomes at R1, where does
        # that leave my third round" -- and a what-if one person is trying
        # should not appear on eleven other screens, let alone be deletable
        # from them. Nothing shared means nothing to authorise.
        #
        # The round trip stays, so the board's numbering is still worked out
        # once, in Python, rather than a second copy of it in the browser.
        held_ids = [] if finished else [int(x) for x in
                    (request.session.get("prep") or {}).get(str(season), [])]
        holds = query(conn, """
            select k.owner_id, k.player_id, k.full_name, k.position, k.cost_round
            from keeper_eligibility k
            where k.for_season = %s and k.player_id = any(%s)
        """, (season, held_ids)) if held_ids else []

        voids = query(conn, """
            select v.owner_id, v.penalty_round, p.full_name, v.confirmed_at
            from keeper_voids v
            join keeper_contracts c on c.contract_id = v.contract_id
            join players p on p.player_id = c.player_id
            where v.season_year = %s
        """, (season,))

    contracts = [e for e in elig
                 if e["state"] == "contract" and e["contract_id"] not in voided]

    # Two keepers wanting one round is not an error, it is a rule: the later
    # arrival moves up to the next free, more expensive round, and a player
    # already under contract never moves. Up the board is down the number,
    # and there is nothing above round one -- which is why two round-one
    # keepers are impossible and say so on /rules.
    #
    # setdefault used to drop the loser of a collision on the floor, so a
    # second waiver pickup at R13 simply never appeared.
    cells = {}
    taken = {}
    placed = {}

    def claim(oid, want, payload, movable):
        """Put a keeper on the board, moving it up if its round is spoken for."""
        seats = taken.setdefault(oid, set())
        got = next_free_round(want, seats) if movable else want
        if got is None:
            return None
        seats.add(got)
        cells[(oid, got)] = dict(payload, round=got, wanted=want)
        return got

    # Contracts and the defence a void forces are fixed points: an existing
    # obligation does not shuffle to make room for a new idea.
    for c in contracts:
        claim(c["owner_id"], c["cost_round"],
              {"name": c["full_name"], "kind": "real", "tag": "contract"}, False)
    for v in voids:
        claim(v["owner_id"], v["penalty_round"],
              {"name": "DEF (void)", "kind": "void", "tag": "void"}, False)
    for r in real:
        placed[("keeper", r["player_id"])] = claim(
            r["owner_id"], r["cost_round"],
            {"name": r["full_name"], "kind": "real", "tag": "keeper"}, True)
    # In the order they were added, so "later" means something.
    for h in sorted(holds, key=lambda x: held_ids.index(x["player_id"])):
        placed[("hold", h["player_id"])] = claim(
            h["owner_id"], h["cost_round"],
            {"name": h["full_name"], "kind": "hold", "tag": "placeholder"}, True)

    # The board is one column per draft slot, not per manager who has taken
    # one. A slot nobody holds still exists and still costs picks; it just
    # does not know yet whose keepers will sit in it.
    slots = list(range(1, team_count + 1))
    holder = {e["slot"]: e for e in entrants if e["slot"]}
    taken_by = {e["owner_id"] for e in entrants if e["slot"]}

    # Slots the reader has put someone in to see how it would look. Session
    # only, like the placeholders, and only ever over an empty slot: a slot
    # a manager has actually chosen is settled and is not the reader's to
    # move. Anything that has since been really taken -- the slot or the
    # manager -- drops out here rather than quietly overriding the truth.
    mock = {}
    for s, oid in ((request.session.get("prepslots") or {}).get(str(season), {})).items():
        s, oid = int(s), int(oid)
        if s in holder or oid in taken_by or s not in slots:
            continue
        who = next((e for e in entrants if e["owner_id"] == oid), None)
        if who and oid not in mock.values():
            mock[s] = oid

    seated = dict(holder)
    for s, oid in mock.items():
        seated[s] = next(e for e in entrants if e["owner_id"] == oid)

    columns = [{"slot": s,
                "owner_id": seated[s]["owner_id"] if s in seated else None,
                "username": seated[s]["username"] if s in seated else None}
               for s in slots]

    # The slots still free to be tried. One someone has really chosen never
    # appears: that one is settled.
    free_slots = [s for s in slots if s not in holder]

    board, n = [], 0
    for rnd in range(1, 14):
        seq = slots if rnd % 2 == 1 else list(reversed(slots))
        row = {}
        for s in seq:
            who = seated.get(s)
            c = cells.get((who["owner_id"], rnd)) if who else None
            if c:
                row[s] = dict(c, pick=None)
            else:
                n += 1
                row[s] = {"name": None, "pick": n,
                          "kind": "open" if who else "unclaimed"}
        board.append({"round": rnd, "cells": [row[s] for s in slots]})

    used = {r["player_id"] for r in real} | {h["player_id"] for h in holds}
    used |= {c["player_id"] for c in contracts}

    # Keyed on managers in lottery order, because every one of them has
    # keepers and only some of them have a slot. The board beside it is keyed
    # on slots; the two answer different questions and do not line up.
    by_owner = []
    for o in entrants:
        oid = o["owner_id"]
        by_owner.append({
            "username": o["username"], "slot": o["slot"], "owner_id": oid,
            "mock_slot": next((s for s, m in mock.items() if m == oid), None),
            "contracts": [c for c in contracts if c["owner_id"] == oid],
            "voids": [v for v in voids if v["owner_id"] == oid],
            "keepers": sorted([dict(r, round=placed.get(("keeper", r["player_id"])))
                               for r in real if r["owner_id"] == oid],
                              key=lambda x: (x["round"] is None, x["round"] or 0)),
            "holds": sorted([dict(h, round=placed.get(("hold", h["player_id"])))
                              for h in holds if h["owner_id"] == oid],
                             key=lambda x: (x["round"] is None, x["round"] or 0)),
            "options": [e for e in elig
                        if e["owner_id"] == oid
                        and e["state"] != "contract"
                        and e["player_id"] not in used],
        })
        # Contracts and approved keepers fill the same slots a placeholder
        # would, so a what-if has to respect the limit the season sets --
        # otherwise the board it draws is one that could never happen. Voids
        # do not count: voiding frees the slot, and the forced defence it
        # costs is a pick, not a keeper.
        row = by_owner[-1]
        row["kept"] = len(row["contracts"]) + len(row["keepers"]) + len(row["holds"])
        row["room"] = max(0, keeper_count - row["kept"])

    token = _page_rings.set(rings) if rings else None
    try:
        return templates.TemplateResponse(
            request=request, name="draft_prep.html",
            context={"years": years, "season": season, "entrants": entrants,
                     "columns": columns, "board": board, "by_owner": by_owner,
                     "chosen": len(holder), "team_count": team_count,
                     "held_count": len(holds),
                     "free_slots": free_slots, "mock": mock,
                     "keeper_count": keeper_count, "finished": finished,
                     "msg": msg, "error": error})
    finally:
        if token is not None:
            _page_rings.reset(token)


@app.post("/draft-prep/placeholder")
async def draft_prep_placeholder(request: Request):
    """Add, drop or clear the reader's own what-ifs.

    Nothing here is shared and nothing is written to the database, so there
    is nobody to authorise against: every path touches one session and that
    session is the caller's. What it used to do -- write a row any of the
    twelve could then delete, with a Clear all that took every manager's
    work in one unconfirmed click -- had no gate of any kind.

    It still posts and redirects rather than doing the work in the browser,
    because the board's pick numbering belongs in one place and that place
    is Python.
    """
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    action = form.get("action")
    key = str(season)

    prep = dict(request.session.get("prep") or {})
    held = [int(x) for x in prep.get(key, [])]
    err = None

    if action == "clear":
        held = []
    elif action == "remove":
        pid = int(form["player_id"])
        held = [x for x in held if x != pid]
    else:
        raw = (form.get("player_id") or "").strip()
        if not raw:
            err = "Pick a player first."
        else:
            pid = int(raw)
            with get_db() as conn:
                rows = query(conn, """
                    select owner_id, cost_round, full_name
                    from keeper_eligibility
                    where for_season = %s and player_id = %s
                """, (season, pid))
                mine = rows[0] if rows else None
                # What that manager already holds, against what the season
                # allows. The template hides the control at the limit; this
                # is the half that cannot be got round.
                limit, kept = 3, 0
                if mine:
                    seats = query(conn, """
                        select keeper_count from seasons where season_year = %s
                    """, (season,))
                    limit = (seats[0]["keeper_count"] if seats else 3) or 0
                    kept = query(conn, """
                        select count(*) n from keeper_eligibility k
                        where k.for_season = %s and k.owner_id = %s
                          and k.state = 'contract'
                          and k.contract_id not in (
                              select contract_id from keeper_voids
                              where season_year = %s)
                    """, (season, mine["owner_id"], season))[0]["n"]
                    kept += query(conn, """
                        select count(*) n from keeper_submissions
                        where season_year = %s and owner_id = %s
                          and status = 'approved' and player_id is not null
                    """, (season, mine["owner_id"]))[0]["n"]
                    kept += len(query(conn, """
                        select 1 from keeper_eligibility
                        where for_season = %s and owner_id = %s
                          and player_id = any(%s)
                    """, (season, mine["owner_id"], held)) if held else [])
                # Where this one would actually land. A round already spoken
                # for is not a refusal -- the newcomer moves up -- but if
                # every round above it is taken as well there is nowhere for
                # him to go, and round one has nothing above it at all.
                landing = mine["cost_round"] if mine else None
                if mine:
                    seats = set()
                    for r in query(conn, """
                        select cost_round from keeper_eligibility k
                        where k.for_season = %s and k.owner_id = %s
                          and k.state = 'contract'
                          and k.contract_id not in (
                              select contract_id from keeper_voids
                              where season_year = %s)
                        union all
                        select penalty_round from keeper_voids
                        where season_year = %s and owner_id = %s
                    """, (season, mine["owner_id"], season, season,
                          mine["owner_id"])):
                        seats.add(r["cost_round"])
                    movers = query(conn, """
                        select s.cost_round from keeper_submissions s
                        where s.season_year = %s and s.owner_id = %s
                          and s.status = 'approved' and s.player_id is not null
                    """, (season, mine["owner_id"]))
                    order = {p: i for i, p in enumerate(held)}
                    movers += sorted(query(conn, """
                        select player_id, cost_round from keeper_eligibility
                        where for_season = %s and owner_id = %s
                          and player_id = any(%s)
                    """, (season, mine["owner_id"], held)) if held else [],
                        key=lambda x: order.get(x["player_id"], 0))
                    for m in movers:
                        got = next_free_round(m["cost_round"], seats)
                        if got:
                            seats.add(got)
                    landing = next_free_round(mine["cost_round"], seats)
            if not mine:
                err = "That player is not eligible."
            elif pid in held:
                err = f"{mine['full_name']} is already on the board."
            elif kept >= limit:
                err = (f"That manager already has {kept} of {limit} keepers. "
                       "Take one off first.")
            elif landing is None:
                err = (f"Nowhere to put {mine['full_name']}. R"
                       f"{mine['cost_round']} and every round above it are "
                       "taken for that manager.")
            else:
                held.append(pid)

    if not err:
        prep[key] = held
        request.session["prep"] = prep

    url = f"/draft-prep?season={season}"
    if err:
        url += "&error=" + quote(err)
    return RedirectResponse(url=url, status_code=303)


@app.post("/draft-prep/slot")
async def draft_prep_slot(request: Request):
    """Seat a manager in an empty slot, or clear one, in the reader's session.

    A slot a manager has actually chosen is settled: it belongs to the draft
    order page, where taking it is a real act in front of eleven other people.
    This only ever fills a slot nobody has taken, and only for a manager who
    has not taken one, so nothing here can contradict the record.
    """
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    key = str(season)
    # Either side may be blank, and either blank means the same thing: take
    # whoever is in this seat out of it. The keeper grid posts a manager and
    # asks for a slot; a board column would post a slot and ask for a
    # manager. One route answers both.
    slot_raw = (form.get("slot") or "").strip()
    raw = (form.get("owner_id") or "").strip()

    book = dict(request.session.get("prepslots") or {})
    here = {int(s): int(o) for s, o in (book.get(key) or {}).items()}
    err = None

    with get_db() as conn:
        real = query(conn, """
            select owner_id, slot from draft_order where season_year = %s
        """, (season,))
    fixed_slots = {r["slot"] for r in real if r["slot"]}
    fixed_owners = {r["owner_id"] for r in real if r["slot"]}
    entered = {r["owner_id"] for r in real}

    slot = int(slot_raw) if slot_raw else None
    oid = int(raw) if raw else None

    if slot is not None and slot in fixed_slots:
        err = f"Slot {slot} has been chosen. That one is settled."
    elif oid is not None and oid in fixed_owners:
        err = "They have already chosen a slot."
    elif slot is None and oid is not None:
        here = {s: o for s, o in here.items() if o != oid}
    elif slot is not None and oid is None:
        here.pop(slot, None)
    elif slot is None and oid is None:
        err = "Nothing to seat."
    else:
        if oid not in entered:
            err = "That manager is not in the draft order."
        else:
            # One seat each, and one manager per seat: putting someone
            # somewhere takes them out of wherever the reader had them, and
            # turfs out whoever the reader had here.
            here = {s: o for s, o in here.items() if o != oid and s != slot}
            here[slot] = oid

    if not err:
        book[key] = {str(s): o for s, o in here.items()}
        request.session["prepslots"] = book

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
        # Voids just became binding, so Oathbreaker may have been earned.
        refresh_keeper_crests(conn, season)

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

        rd = next_free_round(e["cost_round"], taken)
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
        # The route that prompted all this: cancelling a void leaves
        # Oathbreaker standing until something recomputes, and before this
        # the only thing that did was entering a week's scores.
        refresh_keeper_crests(conn, season)

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


def season_setup_steps(conn, year):
    """The eight steps that stand up a league year, in the order the code
    forces, with what is done and what is in the way.

    Written from docs/features/season-setup.md. The dependencies are real
    rather than stylistic: each generator reads the output of the one before
    it, which is why the page reports a blocked step instead of hiding it.
    """
    n = query(conn, """
        select
          (select count(*) from seasons     where season_year = %(y)s) as season_row,
          (select team_count   from seasons where season_year = %(y)s) as team_count,
          (select keeper_count from seasons where season_year = %(y)s) as keeper_count,
          (select count(*) from teams       where season_year = %(y)s) as teams,
          (select count(*) from player_adp  where season_year = %(y)s) as adp,
          (select count(*) from rivalries   where season_year = %(y)s) as rivalries,
          (select count(*) from matchups    where season_year = %(y)s and week <= 14) as sched,
          (select count(*) from draft_order where season_year = %(y)s) as lottery,
          (select count(slot) from draft_order where season_year = %(y)s) as slots,
          (select count(*) from keeper_windows where season_year = %(y)s) as windows,
          (select count(resolved_at) from keeper_windows where season_year = %(y)s) as resolved,
          (select count(*) from draft_picks where season_year = %(y)s) as picks,
          (select count(*) from rosters     where season_year = %(y)s - 1) as prev_rosters
    """, {"y": year})[0]

    size = n["team_count"] or 12
    full_schedule = size // 2 * 14
    teams_done = n["teams"] and n["teams"] == size

    def step(num, title, where, link, state, detail, blockers=()):
        return {"n": num, "title": title, "where": where, "link": link,
                "state": state, "detail": detail, "blockers": list(blockers)}

    steps = []

    steps.append(step(
        1, "The season row", "Still hand-written SQL -- the form belongs here", None,
        "done" if n["season_row"] else "ready",
        ("%s teams, %s keepers each" % (size, n["keeper_count"])
         if n["season_row"] else
         "Nothing exists for %s yet. Everything below hangs off this row." % year)))

    no_season = ["The season row has to exist first"] if not n["season_row"] else []
    steps.append(step(
        2, "Owners and teams", "Still hand-written SQL -- the form belongs here", None,
        "blocked" if no_season else ("done" if teams_done else "ready"),
        ("%s of %s teams" % (n["teams"], size) if n["teams"] else
         "No owner is assigned to %s yet." % year),
        no_season))

    steps.append(step(
        3, "Import ADP", "scripts/import_adp_text.py", None,
        "blocked" if no_season else ("done" if n["adp"] else "ready"),
        ("%s players priced" % n["adp"] if n["adp"] else
         "Needed before keeper rounds resolve, not just before the draft."),
        no_season))

    not_teams = [] if teams_done else ["Every team has to be assigned first"]
    steps.append(step(
        4, "Rivalries", "/admin/rivals", "/admin/rivals",
        "blocked" if (no_season or not_teams) else
        ("done" if n["rivalries"] >= size else "ready"),
        ("%s pairings" % n["rivalries"] if n["rivalries"] else
         "One rival each, mutual, nobody is two managers' rival."),
        no_season + not_teams))

    no_rivals = [] if n["rivalries"] >= size else ["Rivalries decide week 10"]
    steps.append(step(
        5, "Schedule", "/admin/schedule", "/admin/schedule",
        "blocked" if (no_season or not_teams or no_rivals) else
        ("done" if n["sched"] >= full_schedule else "ready"),
        ("%s of %s games" % (n["sched"], full_schedule) if n["sched"] else
         "Fourteen weeks, with week 10 pinned to the rivalries above."),
        no_season + not_teams + no_rivals))

    if not n["lottery"]:
        six = "ready", "Nobody has drawn a ballot yet."
    elif n["slots"] < size:
        six = "part", "Lottery drawn. %s of %s slots chosen." % (n["slots"], size)
    else:
        six = "done", "All %s slots chosen." % size
    steps.append(step(
        6, "Draft order: lottery, then slots", "/draft-order", "/draft-order",
        "blocked" if (no_season or not_teams) else six[0], six[1],
        no_season + not_teams))

    slots_done = n["slots"] >= size and n["lottery"] >= size
    seven_block = list(no_season + not_teams)
    if not n["adp"]:
        seven_block.append(
            "ADP is missing, and a 3-year contract signed without it prices "
            "its later years at round 13 for good")
    if not slots_done:
        seven_block.append("Slots are chosen before keepers, so an owner "
                           "knows where they draft from")
    if not n["prev_rosters"]:
        seven_block.append("Keeper eligibility is drawn from the %s "
                           "end-of-season rosters, which are not loaded" % (year - 1))
    if n["windows"] and n["resolved"] >= n["windows"]:
        seven = "done", "All %s rounds resolved." % n["windows"]
    elif n["windows"]:
        seven = "part", "%s rounds set, %s resolved." % (n["windows"], n["resolved"])
    else:
        seven = "ready", "Three rounds, each with its own window."
    steps.append(step(
        7, "Keeper rounds", "/admin/keepers", "/admin/keepers",
        "blocked" if seven_block else seven[0], seven[1], seven_block))

    eight_block = list(no_season + not_teams)
    if not slots_done:
        eight_block.append("The board needs every slot chosen")
    steps.append(step(
        8, "Import the draft", "scripts/import_draft.py", None,
        "blocked" if eight_block else ("done" if n["picks"] else "ready"),
        ("%s picks" % n["picks"] if n["picks"] else
         "After the draft happens, not during setup."),
        eight_block))

    return steps, n


def write_summary(conn, season, kind, body, model, week=None, matchup_id=None):
    """Store a generated summary, unpublished. Returns its id.

    Replaces the draft that was there, if there was one. Two per thing
    written about is the whole allowance -- one draft and one published --
    and a unique index enforces it, so writing without clearing the old draft
    first is not a lost row, it is an error. See migration 045.

    What has not changed is the part that mattered: a draft is still
    invisible to the league, and publishing is still a separate act.
    """
    with conn.cursor() as cur:
        cur.execute("""
            delete from summaries
            where season_year = %s and kind = %s
              and week is not distinct from %s
              and matchup_id is not distinct from %s
              and published_at is null
        """, (season, kind, week, matchup_id))
        cur.execute("""
            insert into summaries (season_year, kind, week, matchup_id, body, model)
            values (%s, %s, %s, %s, %s, %s)
            returning summary_id
        """, (season, kind, week, matchup_id, body, model))
        new_id = cur.fetchone()["summary_id"]
    conn.commit()
    return new_id


def summary_writer(conn):
    """The `q` a fact gatherer wants: a query that returns dicts.

    app/summaries.py never sees a connection or psycopg -- it is handed this
    and does its own SQL, which is what keeps the domain module free of the
    web app.
    """
    def q(sql, params):
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    return q


def write_one_summary(conn, season, kind, week=None):
    """Generate and store one, unpublished. Returns (id, managers missed)."""
    q = summary_writer(conn)
    if kind == "preview":
        body, missed = summaries.generate_preview(q, season)
    elif kind == "season":
        body, missed = summaries.generate_season(q, season)
    elif kind == "week":
        body, missed = summaries.generate_week(q, season, week)
    else:
        raise summaries.SummaryError("%s summaries are not written yet." % kind)
    return write_summary(conn, season, kind, body, summaries.MODEL,
                         week=week), missed


def week_is_complete(conn, season, week):
    """Every game of a week has a score, byes excepted."""
    rows = query(conn, """
        select count(*) as games,
               count(*) filter (where team_a_points is not null
                                  and (team_b_id is null
                                       or team_b_points is not null)) as scored
        from matchups where season_year = %s and week = %s
    """, (season, week))
    return bool(rows and rows[0]["games"] and
                rows[0]["games"] == rows[0]["scored"])


# Which weeks are being written at this moment. Process-local on purpose: it
# exists so the admin who just saved a week is told something is happening
# rather than that nothing was written, and if the process is restarted
# mid-sentence then nothing was written, which is what an empty set says.
_writing = set()
_writing_lock = threading.Lock()


def week_summary_state(conn, season, week):
    """What there is to say about a week's account, for the scores page.

    One of four: it is being written, there is a draft to read, one has been
    published, or there is nothing -- and in that last case whether the week
    is finished decides between "not yet" and "not going to happen on its
    own".
    """
    rows = query(conn, """
        select summary_id, body, model, generated_at, published_at
        from summaries
        where season_year = %s and kind = 'week' and week = %s
        order by (published_at is not null) desc, generated_at desc
        limit 1
    """, (season, week))
    with _writing_lock:
        busy = (season, week) in _writing
    row = rows[0] if rows else None
    if row:
        state = "published" if row["published_at"] else "draft"
    else:
        state = "writing" if busy else "none"
    return {"state": state, "row": row, "writing": busy,
            "complete": week_is_complete(conn, season, week),
            "have_key": summaries.available()}


def queue_week_summary(season, week, force=False):
    """Write a week's account off the back of the scores that finished it.

    Entering scores is the moment a week becomes something there is anything
    to say about, so it is the moment this fires -- nobody should have to
    remember a second step, and the whole point of generating it now is that
    it is ready to read long before anyone asks for it.

    Three guards, each of which has a cost behind it:

      - a key must be configured, or there is nothing to call;
      - the week must be complete, so a half-entered week does not get an
        account of the three games that were in it;
      - and there must be no summary for that week already. Scores get
        corrected, and the free tier allows twenty calls a day. Without this
        an afternoon of fixing a typo would spend the week's quota on twelve
        accounts of the same six games.

    Off the request thread. The call takes the best part of a minute and the
    admin who entered the scores would otherwise sit through it, and Render
    would very likely time the request out first. The cost is that a process
    which spins down mid-write leaves nothing behind -- so the page says so,
    and writing one by hand is a button.
    """
    if not summaries.available():
        return False
    with get_db() as conn:
        if not week_is_complete(conn, season, week):
            return False
        if not force:
            existing = query(conn, """
                select 1 from summaries
                where season_year = %s and kind = 'week' and week = %s limit 1
            """, (season, week))
            if existing:
                return False

    with _writing_lock:
        if (season, week) in _writing:
            return False
        _writing.add((season, week))

    def run():
        try:
            with get_db() as conn:
                write_one_summary(conn, season, "week", week)
            log.info("wrote the week %s summary for %s", week, season)
        except Exception:
            log.exception("could not write the week %s summary for %s",
                          week, season)
        finally:
            with _writing_lock:
                _writing.discard((season, week))

    threading.Thread(target=run, name="summary-%s-w%s" % (season, week),
                     daemon=True).start()
    return True


# What can be written, in the order the page offers it. The label is what an
# admin calls it; the kind is what the summaries table calls it. Two of the
# five are absent -- a matchup summary needs per-player results the league
# does not keep, and the chat audit needs the chat -- and they are named on
# the page rather than left off it.
SUMMARY_KINDS = (("preview", "Preview"),
                 ("week", "Weekly recap"),
                 ("season", "Recap"))


def summary_target(conn, season, kind, week):
    """The draft and the published summary for one thing written about.

    At most one of each exists -- migration 045 -- so this is two rows at the
    outside, and the page shows whichever of them are there.
    """
    rows = query(conn, """
        select summary_id, kind, week, body, model, generated_at, published_at
        from summaries
        where season_year = %s and kind = %s
          and week is not distinct from %s and matchup_id is null
    """, (season, kind, week))
    return (next((r for r in rows if not r["published_at"]), None),
            next((r for r in rows if r["published_at"]), None))


def summaries_url(season, kind, week=None, msg="", error=""):
    """Back to the thing that was being looked at, not to the top of the page."""
    from urllib.parse import quote
    url = "/admin/summaries?season=%d&kind=%s" % (season, kind)
    if week:
        url += "&week=%d" % week
    if msg:
        url += "&msg=" + quote(msg)
    if error:
        url += "&error=" + quote(error)
    return url


@app.get("/admin/summaries", response_class=HTMLResponse)
def admin_summaries(request: Request, season: int = 0, kind: str = "preview",
                    week: int = 0, msg: str = "", error: str = ""):
    """One thing written about at a time: what exists for it, and what can be
    done to it.

    The page used to list everything a season had and put a writer for each
    kind above it, which meant the answer to "what is week nine doing" was a
    scroll. It asks now: pick the kind, pick the week if the kind wants one,
    and the page is that target and nothing else.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    if kind not in dict(SUMMARY_KINDS):
        kind = "preview"

    with get_db() as conn:
        years = [r["season_year"] for r in query(conn, """
            select season_year from seasons order by season_year desc
        """)]
        if not season:
            season = years[0] if years else 0
        # Which weeks can be written about, and what state each is in. A week
        # with no scores has nothing to say, and offering it would only spend
        # a call finding that out.
        weeks = [r["week"] for r in query(conn, """
            select distinct week from matchups
            where season_year = %s and team_a_points is not null
            order by week
        """, (season,))]
        # Draft and published are different answers to "is this week done
        # with", and one word for both made the picker useless for finding
        # the weeks that still want reading.
        week_state = {}
        for r in query(conn, """
            select week, published_at is not null as live from summaries
            where season_year = %s and kind = 'week' and week is not null
        """, (season,)):
            week_state.setdefault(r["week"], set()).add(
                "published" if r["live"] else "draft")
        written_weeks = {w: " and ".join(sorted(v)) for w, v in week_state.items()}

        # What the season has, as a whole. One target at a time is the right
        # way to work on these and the wrong way to find out which ones still
        # want work, so the count of both goes above the picker.
        tally = {"preview": None, "season": None}
        for r in query(conn, """
            select kind, published_at is not null as live from summaries
            where season_year = %s and kind in ('preview', 'season')
              and matchup_id is null
        """, (season,)):
            tally[r["kind"]] = "published" if r["live"] else "draft"
        weeks_live = sum(1 for v in written_weeks.values() if "published" in v)
        weeks_draft = sum(1 for v in written_weeks.values() if "draft" in v)

        # Whether the year is finished decides whether a recap is a thing that
        # can be written or a thing to wait for. season_results reads the
        # champion off the championship game, so "no champion" has two quite
        # different causes -- the game has not been scored, or the bracket has
        # not been drawn and there is no such game. Saying which saves the
        # reader inferring the wrong one.
        final = query(conn, """
            select count(*) as drawn,
                   count(*) filter (where team_a_points is not null
                                      and team_b_points is not null) as played
            from matchups
            where season_year = %s and game_type = 'championship'
        """, (season,))
        final = final[0] if final else {"drawn": 0, "played": 0}
        finished = bool(final["played"])

        # A week has to be chosen before there is a target at all. Landing on
        # the newest week with scores beats landing on nothing.
        if kind == "week" and week not in weeks:
            week = weeks[-1] if weeks else 0
        if kind != "week":
            week = 0

        draft = published = None
        if kind != "week" or week:
            draft, published = summary_target(conn, season, kind, week or None)

    # What this target is called, said once so the heading, the buttons and
    # the toast cannot drift apart. The heading is capitalised here rather
    # than by Jinja's capitalize, which lowercases everything after the first
    # letter -- harmless while no name holds a proper noun, and silent the day
    # one does.
    if kind == "preview":
        name = "the %s preview" % season
    elif kind == "season":
        name = "the %s recap" % season
    elif week:
        name = "the week %s recap" % week
    else:
        # No week of this season has scores, so there is no week to name.
        name = "a weekly recap"

    # The season at a glance, in one line above the picker.
    def said(state):
        return {"published": "published",
                "draft": "waiting to be read"}.get(state, "not written")

    if not weeks:
        weeks_said = "no week has scores yet"
    else:
        weeks_said = "%d of %d weeks published" % (weeks_live, len(weeks))
        if weeks_draft:
            weeks_said += ", %d waiting to be read" % weeks_draft
    at_a_glance = "Preview %s &middot; %s &middot; recap %s" % (
        said(tally["preview"]), weeks_said, said(tally["season"]))

    # Where a published summary can be read in place. A week's account sits on
    # the page of the week after it -- week nine's read is on the week ten
    # page -- which leaves the last week of a season with nowhere to appear.
    # Said plainly rather than linked to a page that does not exist.
    shown_on = None
    if kind == "preview" and weeks:
        shown_on = "/season/%d?week=%d" % (season, weeks[0])
    elif kind == "season":
        shown_on = "/season/%d?week=season" % season
    elif kind == "week" and week:
        nxt = next((w for w in weeks if w > week), None)
        shown_on = "/season/%d?week=%d" % (season, nxt) if nxt else None

    # A recap needs a champion and a week needs scores. Saying which is
    # missing beats a button that fails after thirty seconds.
    blocked = ""
    if not summaries.available():
        blocked = "No GEMINI_API_KEY is set in this environment."
    elif kind == "season" and not finished:
        blocked = ("%s has no bracket yet, so there is no championship game "
                   "to read a champion from. A recap is written from the "
                   "final table and the bracket." % season
                   if not final["drawn"] else
                   "%s's championship game has no score yet. A recap is "
                   "written from the final table and the bracket." % season)
    elif kind == "week" and not weeks:
        blocked = "No week of %s has scores yet." % season

    return templates.TemplateResponse(
        request=request, name="admin_summaries.html",
        context={"years": years, "season": season, "kinds": SUMMARY_KINDS,
                 "kind": kind, "week": week, "weeks": weeks,
                 "written_weeks": written_weeks, "name": name,
                 "heading": name[:1].upper() + name[1:],
                 "draft": draft, "published": published, "blocked": blocked,
                 "at_a_glance": at_a_glance, "shown_on": shown_on,
                 "model": summaries.MODEL})


@app.post("/admin/summaries/generate")
async def admin_summary_generate(request: Request):
    """Write one, unpublished. Slow on purpose: the call takes twenty seconds
    or so and the admin is standing here waiting for it, which is the whole
    reason the weekly ones are fired by score entry instead."""
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    kind = form.get("kind") or "preview"
    week = int(form.get("week") or 0) or None
    back = "/admin/summaries?season=%d" % season

    if kind not in dict(SUMMARY_KINDS):
        return RedirectResponse(
            url=summaries_url(season, "preview",
                              error="%s summaries are not written yet."
                                    % kind.title()), status_code=303)
    if kind == "week" and not week:
        return RedirectResponse(
            url=summaries_url(season, kind, error="Choose a week to write about."),
            status_code=303)

    with get_db() as conn:
        try:
            _, missed = write_one_summary(conn, season, kind, week)
        except summaries.SummaryError as e:
            # The toast says this once and then deletes itself from the URL,
            # so without this line a failure leaves no trace at all -- the
            # background writer logs its own and this path did not.
            log.warning("could not write the %s summary for %s%s: %s",
                        kind, season, " week %s" % week if week else "", e)
            return RedirectResponse(
                url=summaries_url(season, kind, week, error=str(e)),
                status_code=303)

    what = {"preview": "the %s preview" % season,
            "season": "the %s recap" % season}.get(kind, "the week %s recap" % week)
    note = "Wrote %s. Read it before publishing." % what
    if missed:
        # Reported rather than hidden: the retry already had its go, and an
        # admin deciding whether to publish should know who was left out.
        note += " Not mentioned: %s." % ", ".join(missed)
    return RedirectResponse(url=summaries_url(season, kind, week, msg=note),
                            status_code=303)


def _scores_url(season, week, mode="", msg="", error=""):
    """Back to the week that was being worked on, at the account.

    Built here rather than passed through a form field: a redirect target
    taken from a request is a redirect somebody else can aim.
    """
    from urllib.parse import quote
    url = "/admin/scores?season=%d&week=%d" % (season, week)
    if mode:
        url += "&mode=%s" % quote(mode)
    if msg:
        url += "&msg=" + quote(msg)
    if error:
        url += "&error=" + quote(error)
    return url + "#account"


@app.post("/admin/scores/summary")
async def admin_scores_summary(request: Request):
    """Write the week's account again, from the page the scores were entered on.

    Deliberately not the same thing as the automatic one: this skips the
    guard that stops a corrected week writing itself twice, because asking
    for it again is the whole point. It still runs off the request thread,
    so the answer is the page reloading rather than a minute of nothing.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    form = await request.form()
    season, week = int(form["season"]), int(form["week"])
    mode = form.get("mode") or ""

    if not summaries.available():
        return RedirectResponse(
            url=_scores_url(season, week, mode,
                            error="No GEMINI_API_KEY is set, so nothing can "
                                  "be written."), status_code=303)
    started = queue_week_summary(season, week, force=True)
    if not started:
        with get_db() as conn:
            done = week_is_complete(conn, season, week)
        return RedirectResponse(
            url=_scores_url(season, week, mode,
                            error="Already being written, give it a moment."
                            if done else
                            "Week %d is not finished, so there is nothing to "
                            "write about yet." % week), status_code=303)
    return RedirectResponse(
        url=_scores_url(season, week, mode,
                        msg="Writing the week %d account. It takes about half "
                            "a minute." % week), status_code=303)


@app.post("/admin/summaries/publish")
async def admin_summary_publish(request: Request):
    """Make one visible. The season page reads published rows only, so this
    is the moment a summary becomes something the league can see."""
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    sid = int(form["summary_id"])
    # "scores" is the only other page these can be pressed from, and it is
    # matched rather than followed: a redirect target read out of a form is a
    # redirect somebody else can aim.
    from_scores = form.get("back") == "scores"
    week_back = int(form.get("week") or 0)
    back = "/admin/summaries?season=%d" % season

    # Publishing replaces whatever was published for the same thing. Two
    # statements rather than one: a data-modifying CTE that deletes and then
    # updates the same table gives no ordering guarantee, and the unique
    # index would reject the pair in whichever order it did not like.
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select season_year, kind, week, matchup_id from summaries
                where summary_id = %s and published_at is null
            """, (sid,))
            target = cur.fetchone()
            row = None
            if target:
                cur.execute("""
                    delete from summaries
                    where season_year = %(season_year)s and kind = %(kind)s
                      and week is not distinct from %(week)s
                      and matchup_id is not distinct from %(matchup_id)s
                      and published_at is not null
                """, target)
                cur.execute("""
                    update summaries set published_at = now()
                    where summary_id = %s
                    returning kind, week
                """, (sid,))
                row = cur.fetchone()
        conn.commit()

    if not row:
        if from_scores:
            return RedirectResponse(
                url=_scores_url(season, week_back,
                                error="That account is already published."),
                status_code=303)
        return RedirectResponse(
            url=back + "&error=" + quote("That summary is already published."),
            status_code=303)
    what = ("the week %s recap" % row["week"]) if row["week"] else            ("the %s %s" % (season, "preview" if row["kind"] == "preview" else "recap"))
    if from_scores:
        return RedirectResponse(
            url=_scores_url(season, week_back,
                            msg="Published the week %s account. The league can "
                                "see it now." % row["week"]), status_code=303)
    return RedirectResponse(
        url=summaries_url(season, row["kind"], row["week"],
                          msg="Published %s. The league can see it now." % what),
        status_code=303)


@app.post("/admin/summaries/discard")
async def admin_summary_discard(request: Request):
    """Throw one away.

    A draft by preference: it has never been seen and nothing is lost. The
    published one only when there is no draft left to remove instead, and only
    because there is otherwise no way to unsay something -- publishing replaces
    a published summary but nothing withdraws one. That is a deletion the
    league will notice, so the button that sends it says which it is about to
    do and asks first.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    sid = int(form["summary_id"])
    from_scores = form.get("back") == "scores"
    week_back = int(form.get("week") or 0)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                delete from summaries where summary_id = %s
                returning kind, week, published_at is not null as was_live
            """, (sid,))
            row = cur.fetchone()
        conn.commit()

    if not row:
        if from_scores:
            return RedirectResponse(
                url=_scores_url(season, week_back,
                                error="Nothing to remove: it is already gone."),
                status_code=303)
        return RedirectResponse(
            url="/admin/summaries?season=%d" % season + "&error="
                + quote("Nothing to remove: it is already gone."),
            status_code=303)
    note = ("Removed the published summary. The season page has nothing there "
            "now." if row["was_live"] else "Draft removed.")
    if from_scores:
        return RedirectResponse(
            url=_scores_url(season, week_back, msg=note), status_code=303)
    return RedirectResponse(
        url=summaries_url(season, row["kind"], row["week"], msg=note),
        status_code=303)


@app.get("/admin/season-setup", response_class=HTMLResponse)
def admin_season_setup(request: Request, season: int = 0):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    with get_db() as conn:
        rows = query(conn, "select season_year, is_complete from seasons order by season_year desc")
        known = [r["season_year"] for r in rows]
        # The year to offer next is one past the newest, so a season with no
        # row of its own can still be chosen and stood up.
        nxt = (max(known) + 1) if known else 2022
        years = [nxt] + known
        if not season:
            unfinished = [r["season_year"] for r in rows if not r["is_complete"]]
            season = unfinished[-1] if unfinished else nxt
        steps, counts = season_setup_steps(conn, season)

        # Defaults for a season that does not exist yet, cloned from the most
        # recent one that does. team_count has been 12 and keeper_count 3
        # every year, so cloning is right nearly always and retyping it is
        # only a chance to get it wrong.
        prev = query(conn, """
            select season_year, team_count, keeper_count from seasons
            where season_year < %s order by season_year desc limit 1
        """, (season,))
        prev = prev[0] if prev else {"season_year": None, "team_count": 12,
                                     "keeper_count": 3}

        # Every owner, with last season's team name carried forward. An owner
        # who held a team last year is ticked; a retired one never is. The
        # roster changes by a manager or two a year, so starting from last
        # year's twelve and editing the exceptions is less work than starting
        # from an empty page.
        roster = query(conn, """
            select o.owner_id, o.username, o.is_retired,
                   prev.team_name as carried,
                   now_t.team_name as current_name,
                   (now_t.team_id is not null) as assigned
            from owners o
            left join teams prev on prev.owner_id = o.owner_id
                                and prev.season_year = %s
            left join teams now_t on now_t.owner_id = o.owner_id
                                 and now_t.season_year = %s
            order by o.is_retired, o.username
        """, (prev["season_year"], season))

    done = sum(1 for s in steps if s["state"] == "done")
    return templates.TemplateResponse(
        request=request, name="admin_season_setup.html",
        context={"years": years, "season": season, "steps": steps,
                 "counts": counts, "done": done, "total": len(steps),
                 "prev": prev, "roster": roster})


@app.post("/admin/season-setup/season")
async def admin_create_season(request: Request):
    """Step 1: the seasons row. Every other step hangs off it."""
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    teams = int(form.get("team_count") or 12)
    keepers = int(form.get("keeper_count") or 3)
    back = "/admin/season-setup?season=%d" % season

    err = ""
    if teams % 2:
        # The rivalry and schedule generators both pair the league up.
        err = "An odd team count cannot be paired for rivalries or a schedule."
    elif not 2 <= teams <= 32:
        err = "That team count is not a league."

    if not err:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("select 1 from seasons where season_year = %s", (season,))
                if cur.fetchone():
                    err = "%s already exists." % season
                else:
                    cur.execute("""
                        insert into seasons (season_year, team_count, keeper_count, is_complete)
                        values (%s, %s, %s, false)
                    """, (season, teams, keepers))
            if not err:
                conn.commit()

    if err:
        return RedirectResponse(url=back + "&error=" + quote(err), status_code=303)
    return RedirectResponse(
        url=back + "&msg=" + quote("%s created, %d teams, %d keepers each"
                                   % (season, teams, keepers)), status_code=303)


@app.post("/admin/season-setup/teams")
async def admin_season_teams(request: Request):
    """Step 2: who is in the league this year, and what their team is called.

    Adding and renaming are free. Removing is the careful half: a team_id is
    referenced by matchups, rosters, draft picks, keeper selections and
    transactions, so an owner who already has results against their name is
    kept and reported rather than cascaded away.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    back = "/admin/season-setup?season=%d" % season
    picked = [int(o) for o in form.getlist("owner")]

    with get_db() as conn:
        rows = query(conn, "select team_count from seasons where season_year = %s", (season,))
        if not rows:
            return RedirectResponse(
                url=back + "&error=" + quote("Create the season row first."),
                status_code=303)
        want = rows[0]["team_count"]
        if len(picked) != want:
            return RedirectResponse(
                url=back + "&error=" + quote(
                    "%s needs exactly %d teams and %d were chosen. The rivalry "
                    "and schedule generators both pair the league up."
                    % (season, want, len(picked))), status_code=303)

        existing = {r["owner_id"]: r for r in query(conn, """
            select owner_id, team_id, team_name from teams where season_year = %s
        """, (season,))}

        blocked = []
        added = renamed = removed = 0
        with conn.cursor() as cur:
            for oid in picked:
                name = (form.get("name_%d" % oid) or "").strip()
                if not name:
                    return RedirectResponse(
                        url=back + "&error=" + quote("Every team needs a name."),
                        status_code=303)
                if oid in existing:
                    if existing[oid]["team_name"] != name:
                        cur.execute("""update teams set team_name = %s, updated_at = now()
                                       where season_year = %s and owner_id = %s""",
                                    (name, season, oid))
                        renamed += 1
                else:
                    # team_id is generated always as identity -- never supplied.
                    cur.execute("""insert into teams (season_year, owner_id, team_name)
                                   values (%s, %s, %s)""", (season, oid, name))
                    added += 1

            for oid, row in existing.items():
                if oid in picked:
                    continue
                cur.execute("""
                    select (select count(*) from matchups
                              where team_a_id = %(t)s or team_b_id = %(t)s)
                         + (select count(*) from rosters where team_id = %(t)s)
                         + (select count(*) from draft_picks where team_id = %(t)s)
                         + (select count(*) from keeper_selections where team_id = %(t)s)
                         + (select count(*) from transactions
                              where to_team_id = %(t)s or from_team_id = %(t)s)
                """, {"t": row["team_id"]})
                if cur.fetchone()[0]:
                    blocked.append(row["team_name"])
                else:
                    cur.execute("delete from teams where team_id = %s", (row["team_id"],))
                    removed += 1
        conn.commit()

    if blocked:
        return RedirectResponse(
            url=back + "&error=" + quote(
                "Kept %s: there are already results against that team this "
                "season. Clear those first if the owner really is leaving."
                % ", ".join(blocked)), status_code=303)

    said = []
    for n, word in ((added, "added"), (renamed, "renamed"), (removed, "removed")):
        if n:
            said.append("%d %s" % (n, word))
    return RedirectResponse(
        url=back + "&msg=" + quote("%s teams: %s" % (season, ", ".join(said) or "no change")),
        status_code=303)


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
    rematches, and no pair meeting within `min_gap` weeks.

    The ids are sorted first, so the same seed gives the same schedule
    whatever order the caller read them in. It did not, and that mattered:
    the page previewed a schedule from owners ordered by username and the
    save regenerated it from a query with no order by at all. The two happen
    to agree today. Shuffling the ids and keeping the seed produces a
    different schedule every time, so the agreement was luck and the day it
    broke would have saved a schedule nobody had seen.
    """
    ids = sorted(ids)
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

        # What saving would actually destroy, and what it would leave. Saying
        # "weeks with scores are left alone" is true and is not a number.
        standing = query(conn, """
            select count(*) filter (where team_a_points is null) as replaceable,
                   count(*) filter (where team_a_points is not null) as kept
            from matchups
            where season_year = %s and game_type = 'regular'
        """, (season,))[0]

        # The schedule that is actually in the table. Rolling one and saving it
        # used to be the only way to see anything here, so the answer to "what
        # does 2026 look like" was to roll a fresh one and hope it matched.
        saved_rows = query(conn, """
            select m.week, oa.username as a, ob.username as b
            from matchups m
            join teams ta on ta.team_id = m.team_a_id
            join teams tb on tb.team_id = m.team_b_id
            join owners oa on oa.owner_id = ta.owner_id
            join owners ob on ob.owner_id = tb.owner_id
            where m.season_year = %s and m.game_type = 'regular'
            order by m.week, oa.username
        """, (season,))

        hist = meeting_history(conn, season)

    # A schedule is a whole-season object. The generator lays out fourteen
    # weeks at once and the save has no way to fit that around weeks already
    # played: it deletes only the unscored rows, so a new pairing in a played
    # week lands beside the played one instead of replacing it. Run against
    # 2025 that turns 84 matchups into 159, with twelve games in a week and
    # managers playing twice. So once a score is in, this page stops offering.
    played = bool(query(conn, """
        select 1 from matchups
        where season_year = %s and game_type = 'regular'
          and team_a_points is not null limit 1
    """, (season,)))

    saved = []
    for r in saved_rows:
        if not saved or saved[-1]["week"] != r["week"]:
            saved.append({"week": r["week"], "rival": r["week"] == 10,
                          "games": []})
        saved[-1]["games"].append((r["a"], r["b"]))

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

    team_of = {t["owner_id"]: t["team_id"] for t in teams_list}

    weeks, err, counts, pairing_code = None, None, None, ""
    if seed and not problem and not played:
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
            # The pairings themselves go into the form. The seed was only a
            # recipe, re-cooked at save time against whatever the meeting
            # history, teams and rivalries were by then -- change a rivalry in
            # another tab and you would have saved a schedule you never saw.
            pairing_code = ",".join(
                "%d:%d-%d" % ((w,) + tuple(sorted((team_of[a], team_of[b]))))
                for w in sorted(sched) for a, b in sched[w])

    return templates.TemplateResponse(
        request=request, name="admin_schedule.html",
        context={"years": years, "season": season, "seed": seed,
                 "weeks": weeks, "counts": counts, "error": err or problem,
                 "teams": teams_list, "saved": saved, "played": played,
                 "pairing_code": pairing_code,
                 "replaceable": standing["replaceable"], "kept": standing["kept"]})


@app.post("/admin/schedule/save")
async def admin_schedule_save(request: Request):
    """Write the schedule that was on screen.

    It used to write the seed instead and regenerate from it here, which made
    the saved schedule a function of the teams, the rivalries and the meeting
    history as they stood at save time rather than at preview time. The
    pairings come through the form now and this route only checks them.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    from urllib.parse import quote
    form = await request.form()
    season = int(form["season"])
    seed = int(form.get("seed") or 0)

    def back(err):
        return RedirectResponse(
            url=f"/admin/schedule?season={season}&seed={seed}&error=" + quote(err),
            status_code=303)

    if not form.get("confirm"):
        return back("Tick the confirm box first")

    with get_db() as conn:
        # The rule the page is built around, enforced where it cannot be got
        # round -- a stale seeded tab, or a post by hand.
        if query(conn, """
            select 1 from matchups
            where season_year = %s and game_type = 'regular'
              and team_a_points is not null limit 1
        """, (season,)):
            return back("%d has scores entered. A schedule cannot be rolled over "
                        "a season that has started." % season)

        teams = {t["team_id"] for t in query(conn, """
            select team_id from teams where season_year = %s
        """, (season,))}

        # Parse and check what came back rather than trust it: it arrived
        # through a browser, and a bad pairing here becomes a season nobody
        # can score.
        rows, by_week = [], {}
        for token in (form.get("pairs") or "").split(","):
            try:
                week, pair = token.split(":")
                a, b = (int(x) for x in pair.split("-"))
                week = int(week)
            except ValueError:
                return back("The schedule on the page could not be read. "
                            "Roll another and save that.")
            if a >= b or a not in teams or b not in teams or not 1 <= week <= 14:
                return back("That schedule has a matchup that is not %d's. "
                            "Roll another and save that." % season)
            seen = by_week.setdefault(week, set())
            if a in seen or b in seen:
                return back("Week %d has a manager playing twice. Roll another "
                            "and save that." % week)
            seen.update((a, b))
            rows.append((season, week, "regular", a, b))

        want = len(teams) // 2
        if len(by_week) != 14 or any(len(v) != len(teams) for v in by_week.values()):
            return back("That schedule is not fourteen full weeks of %d games. "
                        "Roll another and save that." % want)

        with conn.cursor() as cur:
            # Nothing has been played -- checked above -- so the whole regular
            # season goes, rather than the unscored part of it.
            cur.execute("""
                delete from matchups
                where season_year = %s and game_type = 'regular'
            """, (season,))
            cur.executemany("""
                insert into matchups
                    (season_year, week, game_type, team_a_id, team_b_id)
                values (%s, %s, %s, %s, %s)
            """, rows)
        conn.commit()

    # No seed: the page that returns shows the saved schedule, which is now
    # the thing that was just written, rather than the proposal it came from.
    return RedirectResponse(
        url=f"/admin/schedule?season={season}&msg=" +
            quote(f"{len(rows)} matchups saved"), status_code=303)


@app.get("/health-check-tail")
def _tail_marker():
    return {"ok": True}



"""Read Yahoo's transaction page.

The page is copied and pasted whole, week after week, so this parses text
rather than a file and says what it could not understand rather than guessing.
Nothing here touches the database: it turns text into blocks and blocks into
moves, and the caller decides what is new.

The shape, as pasted. Each block ends at a line that is just "logo", and the
private-use glyphs Yahoo uses for its little arrows are stripped before any of
this runs, which is why the blocks start at a player:

    Panthers Car - DEF          an add, with the player who made room
    Free Agent
    Titans Ten - DEF
    To Waivers
    Cooper Rush is a Terrorist
    Sep 17, 8:03 am

    Devaughn Vele NO - WR       an add with nobody dropped
    Free Agent
    Finkle is the Mayor
    Sep 16, 6:34 am

A trade is the 2025 shape, which has not been seen in this export yet:
a run of players, then "Traded to", the team, and the date. It is here so a
trade is recognised rather than read as an add; if the real thing turns out to
look different, it lands in `puzzles` and is shown rather than mis-stored.
"""
import collections
import datetime as dt
import re

SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")
# "Taysom Hill NO - QB,TE NA". Two positions because Yahoo lists every one
# a player is eligible at, and a trailing NA or Q or IR-R for his status.
# The first position is the one kept: players.position holds one, and the
# first is the one Yahoo leads with.
POS = "QB|RB|WR|TE|K|DEF"
PLAYER = re.compile(
    r"^(?P<name>.+?)\s+(?P<club>[A-Za-z]{2,3})\s+-\s+"
    r"(?P<pos>" + POS + r")(?P<also>(?:\s*,\s*(?:" + POS + r"))*)"
    r"(?P<status>\s+.*)?$")
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
# What Yahoo prints on the line after a dropped player. The player went
# somewhere; which of the two it was does not change that the team let him go.
GONE = ("to waivers", "to free agents")


def clean(line):
    """Yahoo's icons live in the private use area and carry no meaning here."""
    s = "".join(ch for ch in line if not (0xE000 <= ord(ch) <= 0xF8FF))
    return s.replace(chr(8217), chr(39)).strip()


def norm(name):
    """For matching a name against the database, not for storing.

    Punctuation and generational suffixes are the two things Yahoo and the
    league spell differently from each other.
    """
    s = name.lower().replace(chr(8217), chr(39))
    s = re.sub(r"[.`,]", "", s).replace(chr(39), "")
    s = SUFFIX.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_paren(team):
    return re.sub(r"\s*\(\s*.*?\s*\)\s*$", "", team).strip()


def parse_date(text, season):
    """"Sep 16, 4:52 am" -> a date. The season decides the year.

    A fantasy season runs across new year, so a month before August belongs to
    the season after the one it is filed under.
    """
    m = re.match(r"^([A-Z][a-z]{2})\s+(\d{1,2})", (text or "").strip())
    if not m:
        return None
    month = MONTHS.get(m.group(1))
    if not month:
        return None
    try:
        return dt.date(season if month >= 8 else season + 1,
                       month, int(m.group(2)))
    except ValueError:
        return None


def method_of(text):
    """Free agent, waiver, a FAAB bid, or the commissioner stepping in."""
    low = text.lower()
    if low.startswith("$"):
        return "faab", re.sub(r"[^0-9.]", "", text.split()[0]) or "0"
    if "commissioner" in low:
        return "commissioner", None
    if low.startswith("waiver"):
        return "waiver", None
    return "free_agent", None


def blocks(text):
    """The pasted page, split on the "logo" line that ends each entry."""
    out, current = [], []
    for raw in text.replace("\r", "").split("\n"):
        line = clean(raw)
        if line == "logo":
            if current:
                out.append(current)
            current = []
        elif line:
            current.append(line)
    if current:
        out.append(current)
    return out


def player_of(line):
    m = PLAYER.match(line)
    return (m.group("name").strip(), m.group("pos")) if m else (None, None)


def parse(text, season, expect="adds"):
    """Every move in the paste, and every block that made no sense.

    `expect` says which kind of page this is, because they are pasted into
    separate boxes. A block of the other shape is not quietly reinterpreted:
    it becomes a puzzle saying which box it belongs in. Keeping them apart is
    what stops a trade being read as an add by a parser willing to try.

    Returns (moves, puzzles). A move is a dict the caller can match against
    the database; a puzzle is {"lines", "why"}, the lines kept verbatim so the
    admin sees what was skipped rather than wondering where it went.
    """
    moves, puzzles, trade_sides = [], [], []

    def puzzle(block, why):
        puzzles.append({"lines": block, "why": why})

    for block in blocks(text):
        # ---- a trade, in the shape 2025 was imported from ----
        if "Traded to" in block:
            if expect != "trades":
                puzzle(block, "This is a trade. Paste it in the trades box.")
                continue
            at = block.index("Traded to")
            players = [player_of(l) for l in block[:at]]
            if (len(block) >= at + 3 and all(n for n, _ in players)
                    and players):
                trade_sides.append({"players": players,
                                    "team": strip_paren(block[at + 1]),
                                    "date": block[at + 2]})
            else:
                puzzle(block, "A trade with no players read from it.")
            continue

        if expect == "trades":
            puzzle(block, "No “Traded to” in this block. If it is a waiver "
                          "or a free agent pickup, paste it in the box above.")
            continue

        # ---- an add, with or without the player who made room ----
        name, pos = player_of(block[0]) if block else (None, None)
        if not name:
            puzzle(block, "The first line is not a player.")
            continue

        if len(block) == 4:
            _, method_text, team, date = block
            dropped = None
        elif len(block) == 6 and block[3].lower() in GONE:
            _, method_text, drop_line, _, team, date = block
            dropped = player_of(drop_line)
            if not dropped[0]:
                puzzle(block, "The dropped player could not be read.")
                continue
        else:
            puzzle(block, "%d lines, and not a shape this knows." % len(block))
            continue

        method, faab = method_of(method_text)
        team = strip_paren(team)
        moves.append({"kind": "add", "player": name, "position": pos,
                      "team": team, "method": method, "faab": faab,
                      "date": date, "on": parse_date(date, season),
                      "raw": block})
        if dropped:
            moves.append({"kind": "drop", "player": dropped[0],
                          "position": dropped[1], "team": team,
                          "method": None, "faab": None,
                          "date": date, "on": parse_date(date, season),
                          "raw": block})

    # ---- trades pair up by date ----
    # One row per player moved, in both directions, which is how the season
    # crest counts a three-for-one as one trade rather than four.
    by_date = collections.defaultdict(list)
    for side in trade_sides:
        by_date[side["date"]].append(side)
    for date, sides in by_date.items():
        if len(sides) != 2:
            for side in sides:
                puzzle(["Traded to " + side["team"], date],
                       "%d side to this trade, expected 2. A trade needs both "
                       "halves pasted together." % len(sides))
            continue
        a, b = sides
        for side, other in ((a, b), (b, a)):
            for name, pos in side["players"]:
                moves.append({"kind": "trade", "player": name, "position": pos,
                              "team": side["team"], "from_team": other["team"],
                              "method": None, "faab": None, "date": date,
                              "on": parse_date(date, season),
                              "raw": ["%s: %s to %s" % (date, name, side["team"])]})
    return moves, puzzles


def resolve(moves, teams, players, existing, season):
    """Sort parsed moves into what can be written and what cannot.

    Everything here is a lookup against dictionaries the caller built, so this
    stays testable without a database.

      ready       rows to insert, in the order they were pasted
      known       rows already stored -- the overlap that makes a weekly
                  paste safe, and the proof that nothing fell between two
                  pastes
      unresolved  a team the league does not have, which is almost always a
                  manager renaming a team mid-season
      wanted      players the league has never seen, to be created on the
                  admin's say-so: a rookie picked up in September was never
                  drafted and was on nobody's roster in January
    """
    ready, known, unresolved, wanted = [], [], [], {}
    for m in moves:
        to_name = m["team"]
        from_name = m.get("from_team")
        tid = teams.get(norm(to_name))
        fid = teams.get(norm(from_name)) if from_name else None

        missing = [n for n, got in ((to_name, tid), (from_name, fid))
                   if n and got is None]
        if missing:
            unresolved.append(dict(m, why="no team called " +
                                   " or ".join(repr(n) for n in missing)))
            continue

        # Which way the player moved decides which end of the row is filled.
        if m["kind"] == "add":
            to_id, from_id = tid, None
        elif m["kind"] == "drop":
            to_id, from_id = None, tid
        else:
            to_id, from_id = tid, fid

        pid = players.get(norm(m["player"]))
        if pid is None:
            wanted[norm(m["player"])] = (m["player"], m["position"])

        row = dict(m, player_id=pid, to_team_id=to_id, from_team_id=from_id)
        # A player the league has never seen cannot already have a row, so
        # there is nothing to compare and it is new by definition.
        key = (season, m["kind"], pid, to_id, from_id, m["date"])
        if pid is not None and key in existing:
            known.append(row)
        else:
            ready.append(row)
    return ready, known, unresolved, wanted


def continuity(ready, known, newest_stored):
    """Whether the paste joins up with what is already there.

    A paste says what is new; it cannot say what is missing. Yahoo shows
    twenty-five transactions to a page, so a busy fortnight can push older
    ones off the end of it, and those would be absent with nothing to show
    for it -- a hole that becomes a wrong keeper price, because the cost
    basis counts any add at all.

    An overlap settles it: if anything in the paste is already stored, then
    nothing happened between the two that was not seen. Without an overlap,
    the oldest row pasted and the newest row stored are compared, and a space
    between them is reported rather than assumed away.
    """
    dated = [r["on"] for r in ready + known if r["on"]]
    oldest = min(dated) if dated else None
    if known:
        return {"state": "joined", "oldest": oldest, "newest_stored": newest_stored}
    if newest_stored is None:
        return {"state": "first", "oldest": oldest, "newest_stored": None}
    if oldest and oldest > newest_stored:
        return {"state": "gap", "oldest": oldest, "newest_stored": newest_stored}
    return {"state": "joined", "oldest": oldest, "newest_stored": newest_stored}

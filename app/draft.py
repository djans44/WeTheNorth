"""Read a draft board as Yahoo prints it.

Pasted into the site rather than read from a file, the same way transactions
are, and like that one it parses text and reports what it could not read
instead of guessing. Nothing here touches the database.

Yahoo has printed the board two ways, and both are here because the older
seasons were loaded from the first:

    1<tab>Bijan Robinson<tab>Right Where You Dak Me       one line

    1<tab>Bijan Robinson                                  three lines
    (Atl - RB)
    Right Where You Dak Me

Two things about those lines matter.

The badge that can follow a player's name is Yahoo's keeper mark, and it is
the only place the board says which picks were kept -- 35 of them in 2025,
matching the 35 rows in the league's own keeper_selections exactly. It lives
in the private use area, which is also where Yahoo's decorative icons live, so
it has to be read *before* those are stripped. The old file importer stripped
first and lost it.

And only the three-line shape carries a position. That decides whether a
player the league has never seen can be created from the paste alone or has to
be asked about, because players.position cannot be null.
"""
import re

KEEPER_MARK = ""
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
ROUND = re.compile(r"^Round\s+(\d+)\s*$")
PICK = re.compile(r"^(\d+)\.\s*(.+)$")
# "(Atl - RB)" under a player, in the three-line shape, and "(NO - QB,TE)"
# when Yahoo lists every position he is eligible at. The first is the one
# kept, because players.position holds one.
POSITION = re.compile(
    r"^\((?P<club>.+?)\s+-\s+(?P<pos>QB|RB|WR|TE|K|DEF)"
    r"(?P<also>(?:\s*,\s*(?:QB|RB|WR|TE|K|DEF))*)\)$")


def clean(line):
    """Drop Yahoo's private-use icons. Call only after the badge is read."""
    s = "".join(ch for ch in line if not (0xE000 <= ord(ch) <= 0xF8FF))
    return s.replace(chr(8217), chr(39)).strip()


def norm(name):
    """Match the spelling rules the rest of the site matches on."""
    s = name.lower().replace(chr(8217), chr(39))
    s = re.sub(r"[.`,]", "", s).replace(chr(39), "")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse(text):
    """Every pick on the board, and every line that made no sense.

    Returns (picks, puzzles). A pick is round, pick_in_round, player, team,
    position (None in the one-line shape) and whether it was kept; a puzzle is
    {"line", "why"}.
    """
    picks, puzzles, rnd = [], [], None

    # The badge is kept beside the cleaned text, because clean() takes it.
    rows = [(clean(raw), KEEPER_MARK in raw)
            for raw in text.replace("\r", "").split("\n")]

    def next_filled(start):
        j = start
        while j < len(rows) and not rows[j][0]:
            j += 1
        return j

    i = 0
    while i < len(rows):
        line, kept = rows[i]
        if not line:
            i += 1
            continue

        m = ROUND.match(line)
        if m:
            rnd = int(m.group(1))
            i += 1
            continue

        m = PICK.match(line)
        if not m:
            puzzles.append({"line": line,
                            "why": "Not a round heading and not a pick."})
            i += 1
            continue
        if rnd is None:
            puzzles.append({"line": line,
                            "why": "A pick before any “Round N” line."})
            i += 1
            continue

        rest = re.split(r"\t", m.group(2))
        if len(rest) < 2:
            rest = re.split(r"\s{2,}", m.group(2))

        if len(rest) >= 2:
            # One line: the player and the team, no position anywhere.
            player, team, position = clean(rest[0]), clean(rest[-1]), None
            i += 1
        else:
            # Three lines. The position and the manager are looked for where
            # they should be, and a puzzle is raised rather than a guess made
            # if either is not there.
            player, position, team = clean(rest[0]), None, None
            j = next_filled(i + 1)
            got = POSITION.match(rows[j][0]) if j < len(rows) else None
            if not got:
                puzzles.append({"line": line,
                                "why": "No team on this line, and no "
                                       "“(Team - POS)” under it."})
                i += 1
                continue
            position = got.group("pos")
            j = next_filled(j + 1)
            if (j >= len(rows) or PICK.match(rows[j][0])
                    or ROUND.match(rows[j][0])):
                puzzles.append({"line": line,
                                "why": "No manager under the position line."})
                i += 1
                continue
            team = rows[j][0]
            i = j + 1

        if not player or not team:
            puzzles.append({"line": line, "why": "A blank player or team."})
            continue

        picks.append({"round": rnd, "pick": int(m.group(1)),
                      "player": player, "team": team, "keeper": kept,
                      "position": position})
    return picks, puzzles


def check(picks, teams, season_teams):
    """What is wrong with the board as a whole, in plain sentences.

    A draft is one shape: every round the same length, every manager picking
    once a round, nobody drafted twice. Saying which of those is broken beats
    a foreign key error after the fact.
    """
    problems = []
    if not picks:
        return ["Nothing on the board."]

    unknown = sorted({p["team"] for p in picks if norm(p["team"]) not in teams})
    for name in unknown:
        problems.append("No team called %r in %d." % (name, season_teams["year"]))

    rounds = sorted({p["round"] for p in picks})
    if rounds != list(range(1, len(rounds) + 1)):
        problems.append("Rounds run %s, which is not 1 to %d with none missing."
                        % (", ".join(str(r) for r in rounds), len(rounds)))

    size = season_teams["count"]
    for r in rounds:
        in_round = [p for p in picks if p["round"] == r]
        slots = sorted(p["pick"] for p in in_round)
        if slots != list(range(1, size + 1)):
            problems.append("Round %d has picks %s; expected 1 to %d."
                            % (r, ", ".join(str(s) for s in slots), size))
        drafting = [norm(p["team"]) for p in in_round]
        twice = sorted({t for t in drafting if drafting.count(t) > 1})
        if twice:
            problems.append("Round %d has %d manager%s picking twice."
                            % (r, len(twice), "" if len(twice) == 1 else "s"))

    seen = {}
    for p in picks:
        seen.setdefault(norm(p["player"]), []).append(p)
    for name, rows in sorted(seen.items()):
        if len(rows) > 1:
            problems.append("%s is drafted %d times."
                            % (rows[0]["player"], len(rows)))

    return problems


def resolve(picks, teams, players):
    """Attach the ids, and say which players the league has never seen.

    Each unknown carries the key it is filed under as well as its name, so the
    form field that asks for a position is named by the same rule that looks
    the answer up. Deriving that key twice -- once here, once in a template --
    is how the two drift apart and the answer quietly goes missing.

    It also carries the position the board gave, when the board gave one, and
    then there is nothing to ask.
    """
    rows, wanted = [], {}
    for p in picks:
        pid = players.get(norm(p["player"]))
        if pid is None:
            key = norm(p["player"])
            # First sighting wins, except that one with a position beats one
            # without.
            if key not in wanted or (p["position"]
                                     and not wanted[key]["position"]):
                wanted[key] = {"key": key, "name": p["player"],
                               "position": p["position"]}
        rows.append(dict(p, player_id=pid, team_id=teams.get(norm(p["team"]))))
    return rows, [wanted[k] for k in sorted(wanted)]

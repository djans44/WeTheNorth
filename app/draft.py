"""Read a draft board as Yahoo prints it.

Pasted into the site rather than read from a file, the same way transactions
are, and like that one it parses text and reports what it could not read
instead of guessing. Nothing here touches the database.

The shape:

    Round 1
    1.<tab>Bijan Robinson<tab>It's Me Mr. Jaxon! Hee-Hee!
    2.<tab>Saquon Barkley<tab>Go Long London
    5.<tab>Joe Burrow <tab>I'm stuck Step-Burrow

    Round 2
    ...

Two things about that last line matter.

The glyph after Joe Burrow is Yahoo's little keeper badge, and it is the only
place the board says which picks were keepers -- 35 of them in 2025, matching
the 35 rows in the league's own keeper_selections exactly. It lives in the
private use area, which is also where Yahoo's decorative icons live, so it has
to be looked for *before* those are stripped. The old file importer stripped
first and lost it.

And the board carries no positions. A player the league has never seen cannot
be created from this text alone, so the page asks for the position rather than
choosing one.
"""
import re

KEEPER_MARK = ""
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
ROUND = re.compile(r"^Round\s+(\d+)\s*$")
PICK = re.compile(r"^(\d+)\.\s*(.+)$")


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

    Returns (picks, puzzles). A pick is round, pick_in_round, player, team and
    whether it was kept; a puzzle is {"line", "why"}.
    """
    picks, puzzles, rnd = [], [], None

    for raw in text.replace("\r", "").split("\n"):
        # The badge is read from the raw line, because clean() would take it.
        kept = KEEPER_MARK in raw
        line = clean(raw)
        if not line:
            continue

        m = ROUND.match(line)
        if m:
            rnd = int(m.group(1))
            continue

        m = PICK.match(line)
        if not m:
            puzzles.append({"line": line,
                            "why": "Not a round heading and not a pick."})
            continue
        if rnd is None:
            puzzles.append({"line": line,
                            "why": "A pick before any “Round N” line."})
            continue

        # Tabs are what Yahoo uses; runs of spaces are what survives a paste
        # through something that ate them.
        parts = re.split(r"\t", m.group(2))
        if len(parts) < 2:
            parts = re.split(r"\s{2,}", m.group(2))
        if len(parts) < 2:
            puzzles.append({"line": line,
                            "why": "No gap between the player and the team."})
            continue

        player = clean(parts[0])
        team = clean(parts[-1])
        if not player or not team:
            puzzles.append({"line": line, "why": "A blank player or team."})
            continue

        picks.append({"round": rnd, "pick": int(m.group(1)),
                      "player": player, "team": team, "keeper": kept})
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
            problems.append("%s is drafted %d times." % (rows[0]["player"], len(rows)))

    return problems


def resolve(picks, teams, players):
    """Attach the ids, and say which players the league has never seen.

    Each unknown carries the key it is filed under as well as its name, so the
    form field that asks for a position is named by the same rule that looks
    the answer up. Deriving that key twice -- once here, once in a template --
    is how the two drift apart and the answer quietly goes missing.
    """
    rows, wanted = [], {}
    for p in picks:
        pid = players.get(norm(p["player"]))
        if pid is None:
            wanted[norm(p["player"])] = p["player"]
        rows.append(dict(p, player_id=pid, team_id=teams.get(norm(p["team"]))))
    return rows, [{"key": k, "name": n} for k, n in sorted(wanted.items())]

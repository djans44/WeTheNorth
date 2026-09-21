# -*- coding: utf-8 -*-
"""Read a pasted average-draft-position list.

Text in, rows out, no database. The web page does the matching and the
writing; this only has to work out what each line says and say so when it
cannot.

ADP is not a record of the league -- it is a reference table copied in from
outside it, and the two differ in a way that matters here: a list of three
hundred players will always name people this league has never heard of, and
kickers, which it does not play at all. So a name that does not match is
reported and skipped rather than treated as a fault, which is the opposite
of the roster loader, where an unknown name means a move was never loaded.
"""
import re

from app import transactions as txn

# The positional rank a provider prints beside each player -- QB1, RB12,
# DST3. It is what identifies a line as a player row rather than a header,
# a page number or a blank.
RANK = re.compile(r"^(QB|RB|WR|TE|K|DST|DEF)\s*(\d+)$", re.I)

# A draft position. Whole picks and one decimal place are both printed.
NUMBER = re.compile(r"^\d+(\.\d+)?$")

# Anything number-shaped at all, used only to tell "this line has no
# position on it" from "the position on it is not one".
ANY_NUMBER = re.compile(r"-?\d+(\.\d+)?")

# A team abbreviation column, which is not a player name.
TEAM = re.compile(r"^[A-Z]{2,3}$")


def _fields(line):
    """Tabs if there are any, runs of spaces otherwise.

    A paste out of a browser table is tab separated; one out of a PDF or a
    fixed-width view is not, and both turn up.
    """
    if "\t" in line:
        return [f.strip() for f in line.split("\t")]
    return [f.strip() for f in re.split(r"\s{2,}", line)]


def parse(text):
    """Every player row in a paste, and every line that is not one.

    The columns are found by what they look like rather than by position:
    providers order them differently and add tiers, byes and vs-ranks, and a
    loader written against one column order meets the other one.
    """
    rows, puzzles = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        fields = [f for f in _fields(line) if f]
        if not fields:
            continue

        rank = next((f for f in fields if RANK.match(f)), None)
        if not rank:
            # No positional rank: a heading, a page marker, a footnote. Not
            # worth naming one by one -- a paste has plenty -- so these are
            # counted rather than listed.
            continue

        # Positive, and not the digits inside the rank itself: QB1 is a
        # rank, not a first pick. Zero is refused here rather than by the
        # database, which has the same rule and answers it with a 500.
        rest = [f for f in fields if f is not rank]
        numbers = [f for f in rest if NUMBER.match(f) and float(f) > 0]
        names = [f for f in rest
                 if not NUMBER.match(f) and not TEAM.match(f)
                 and re.search(r"[A-Za-z]", f)]
        if not numbers:
            # There may well be a number on it -- a negative, a fraction
            # written oddly -- and saying "no draft position" about a line
            # that plainly has one sends the reader looking for the wrong
            # thing. A position is a positive number and nothing else.
            why = ("no draft position on the line"
                   if not any(ANY_NUMBER.search(f) for f in rest)
                   else "a draft position has to be a number above zero")
            puzzles.append({"line": line, "why": why})
            continue
        if not names:
            puzzles.append({"line": line, "why": "no player name on the line"})
            continue

        # Two shapes, told apart rather than guessed between. A line can
        # carry more than one number -- the bye week sits beside the draft
        # position -- and taking the first or the last of them at random gets
        # it right about half the time.
        #
        #   rank, position, name, ...   the shape the league's own paste has
        #   rank, name, ..., position   the shape a provider's table has
        #
        # Which one is in front decides it: a number straight after the rank
        # is the draft position, and otherwise the draft position is the last
        # number on the line, after the team and the bye.
        if rest and NUMBER.match(rest[0]) and float(rest[0]) > 0:
            pick = float(rest[0])
        else:
            pick = float(numbers[-1])

        # The longest run of letters is the name: a provider prints the team
        # and the bye beside it, and those are short or numeric.
        name = max(names, key=len)
        rows.append({"name": name, "adp": pick,
                     "rank": RANK.match(rank).group(0).upper()})
    return rows, puzzles


def resolve(rows, players, defences):
    """Split parsed rows into the ones this league has and the ones it does not.

    `players` maps a normalised name to a player_id; `defences` maps a
    normalised last word to one, because the league stores a defence as a
    bare nickname -- "Bears" -- and a provider prints "Chicago Bears".

    A name appearing twice keeps its first position. Providers repeat a
    player across positional lists more often than you would think.
    """
    matched, unmatched, seen = [], [], set()
    for r in rows:
        key = _norm(r["name"])
        pid = players.get(key)
        if pid is None and r["rank"].startswith(("DST", "DEF")):
            pid = defences.get(key.split()[-1]) if key else None
        if pid is None:
            unmatched.append(r)
            continue
        if pid in seen:
            continue
        seen.add(pid)
        matched.append(dict(r, player_id=pid))
    return matched, unmatched


# The league already has a rule for matching a printed name against its own
# -- punctuation and generational suffixes are what sources spell
# differently. Borrowed rather than repeated, so a change to it reaches every
# loader at once.
_norm = txn.norm

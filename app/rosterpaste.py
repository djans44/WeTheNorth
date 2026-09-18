"""Read the twelve rosters as Yahoo prints them at the end of a season.

Kept apart from app/rosters.py, which works a roster out from the draft and
the moves. This reads one that somebody copied off a screen. The two meet in
the admin page, where the point is the difference between them.

The shape, per team:

    It's Me Mr. Jaxon! Hee-Hee!
    Pos<tab>Player
    QB<tab>
    Caleb Williams
    Caleb WilliamsVideo Forecast
    Final L 38-42 @ SF
    WR<tab>
    Jaxon Smith-Njigba
    ...

A slot line ends in a tab and is one of SLOTS; the line after it is the
player. Everything between one slot and the next is Yahoo's own furniture --
the name repeated with "Video Forecast" stuck to it, the result of the game,
a blank line -- and is stepped over rather than guessed at. The team is the
last thing said before the Pos/Player header.

Nothing here touches the database.
"""

# Where a player sat at the end. BN is the bench and IR the injured list, and
# both are on the roster -- which is why a squad runs to fifteen rather than
# the thirteen that start one.
SLOTS = ("QB", "WR", "RB", "TE", "W/T", "W/R/T", "Q/W/R/T", "K", "DEF",
         "BN", "IR")


def strip_icons(line):
    """Yahoo's private-use glyphs carry nothing here."""
    out = "".join(ch for ch in line if not (0xE000 <= ord(ch) <= 0xF8FF))
    return out.replace(chr(8217), chr(39))


def is_header(line):
    """The Pos/Player row that starts a team, whatever whitespace survived."""
    return line.replace("\t", "").replace(" ", "") == "PosPlayer"


def is_slot(raw):
    """A slot line: the label, then a tab, then nothing."""
    return raw.rstrip("\n").endswith("\t") and raw.strip() in SLOTS


def parse(text):
    """Every roster in the paste, and every line that made no sense.

    Returns (squads, puzzles). A squad is {"team", "players": [{slot, name}]};
    a puzzle is {"line", "why"}.
    """
    rows = [strip_icons(l) for l in text.replace("\r", "").split("\n")]
    squads, puzzles = [], []
    current, last_said = None, None

    i = 0
    while i < len(rows):
        raw = rows[i]
        line = raw.strip()

        if is_header(line):
            # A new team, named by the last line that said anything.
            if not last_said:
                puzzles.append({"line": line,
                                "why": "A roster with no team named above it."})
            else:
                current = {"team": last_said, "players": []}
                squads.append(current)
            i += 1
            continue

        if is_slot(raw):
            if current is None:
                puzzles.append({"line": line, "why": "A slot before any team."})
                i += 1
                continue
            j = i + 1
            while j < len(rows) and not rows[j].strip():
                j += 1
            if j >= len(rows):
                puzzles.append({"line": line, "why": "A slot with no player."})
                break
            if is_slot(rows[j]) or is_header(rows[j].strip()):
                # An empty slot: nobody sat there at the end.
                i = j
                continue
            current["players"].append({"slot": line, "name": rows[j].strip()})
            i = j + 1
            continue

        if line:
            last_said = line
        i += 1

    for s in squads:
        if not s["players"]:
            puzzles.append({"line": s["team"],
                            "why": "A team with nobody on it."})
    return squads, puzzles

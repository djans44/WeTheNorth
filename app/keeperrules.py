"""Keeper rules that are not about the web, kept where a script can reach them.

`app/crests.py` is the precedent: domain logic in its own module rather than
buried in main.py. Nothing here imports FastAPI, so a hand-run script can use
it without standing up the application.
"""


def next_free_round(want, taken):
    """The round a keeper actually costs when its own is spoken for.

    From /rules: no two of your keepers may cost the same round, and the one
    who arrived later moves **up** to the next free, more expensive round.
    Up the board is down the number, and there is nothing above round one --
    which is why two round-one keepers are impossible. None says there was
    nowhere left to go.

    This is the only copy of the rule in Python. It was four inside main.py
    alone -- two named helpers taking the same two arguments in opposite
    orders, and two generator expressions inlined where they were needed --
    plus a fifth in scripts/resolve_phase.py, which is why this now lives in a
    module a script can import rather than in the web app.

    firstFree() in static/keepers.js is the one copy that has to stay: the
    card redraws as an owner clicks and cannot ask the server between clicks.
    It takes its arguments in this order so the two read alike. This one
    decides. Nothing is written without it, so a divergence shows up as a
    preview that lied rather than as a keeper in the wrong round.
    """
    for r in range(want, 0, -1):
        if r not in taken:
            return r
    return None

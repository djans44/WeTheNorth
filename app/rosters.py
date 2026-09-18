"""Who is on a roster, worked out rather than stored.

`rosters` holds one snapshot a year, taken at the end of it. That is what
keepers are drawn from and it is the only roster the league has ever kept, so
for most of a season the answer to "who has Josh got" was nowhere.

It does not have to be stored. The draft says who started where and the
transactions say every move since, so the roster on any date is the one and
the other applied in order. That makes the end-of-season snapshot a *check* on
the record instead of the record itself: import it, compare, and a difference
is a gap in the transactions worth finding -- which matters because
keeper_cost_basis counts any add at all, so a missing one is a wrong price.

Size proves nothing on its own. IR slots mean a squad can legitimately carry
anywhere from thirteen to fifteen, so a roster that is a player over is not
evidence of a missing drop; comparing against a loaded roster is, and that is
the check worth building.

Only from 2026. The 2022-2025 transactions were loaded as adds alone -- drops
were not recorded until the paste page was built -- so applying them would put
players on rosters and never take any off, and every squad would grow all
year. Those seasons have one honest answer and it is the stored snapshot. A
season is derivable here when it has a draft and transactions of its own;
otherwise the snapshot is what there is, and when there is no snapshot either
the page says so rather than showing a roster nobody should trust.

Nothing here touches the database. It takes rows and returns who holds what.
"""
import collections

# How a player came to be where he is, in the order the page shows them and
# the order they happened.
KEPT, DRAFTED, ADDED, TRADED = "kept", "drafted", "added", "traded"


def derive(picks, moves):
    """The roster of every team, from the draft forward.

    `picks` are the season's draft rows; `moves` are its transactions in the
    order they happened, oldest first. A drop takes a player off a roster and
    puts him on none -- free agents are not tracked, only squads.

    Returns {team_id: [player rows]}, each carrying how and when it arrived.
    """
    held = {}
    for p in picks:
        if p["player_id"] is None:
            continue
        held[p["player_id"]] = {
            "player_id": p["player_id"], "team_id": p["team_id"],
            "how": KEPT if p["is_keeper"] else DRAFTED,
            "round": p["round"], "when": None,
            "full_name": p.get("full_name"), "position": p.get("position")}

    for m in moves:
        pid = m["player_id"]
        if m["kind"] == "drop":
            held.pop(pid, None)
            continue
        held[pid] = {
            "player_id": pid, "team_id": m["to_team_id"],
            "how": TRADED if m["kind"] == "trade" else ADDED,
            # A traded player keeps the round he was drafted in -- the basis
            # follows the player -- but an added one has none.
            "round": held.get(pid, {}).get("round") if m["kind"] == "trade" else None,
            "when": m["occurred_on"],
            "full_name": m.get("full_name"), "position": m.get("position")}

    out = collections.defaultdict(list)
    for row in held.values():
        out[row["team_id"]].append(row)
    return dict(out)

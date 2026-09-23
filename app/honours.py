"""The four honours the league votes on, and the ballots they are voted from.

Named in Song, Legend of the Choosing, The Bargain of the Age, The Red Week.
No rule computes them -- they are opinions -- so the league is asked.

Every candidate resolves to exactly one owner, because a crest is held by a
person. A team name belongs to its manager; a defeat belongs to whoever
suffered it; a late pick belongs to whoever made it; a trade has two sides and
each is its own candidate, so the vote decides who receives it rather than
leaving that to be settled afterwards.

All four read a season as far as it has been played. The late pick used to
need the end-of-season roster as well. It does not: a player nobody moved is
still where the draft put him, and the assembly sits during the playoffs,
weeks before those rosters are loaded.

Nothing here writes. It reads a season and returns the four ballots.
"""

# The margin under which a defeat is close enough to have been undeserved.
# Ten points is the league's own sense of it; the whole season is 102 games
# and around a quarter of them come in under this, which is a ballot rather
# than a list.
CLOSE = 10

# Where the late rounds begin. Ten of thirteen, so the last four.
LATE_FROM = 10

# The last week of the regular season, and the week the late pick waits for.
# Not because the answer changes much afterwards -- it barely does -- but
# because before it the ballot is every late pick nobody has got round to
# dropping yet, which in week two is thirty-six of them and in week fourteen
# is the handful the crest is actually about. A ballot that wide is not a
# shortlist, it is the draft board with the top nine rounds taken off.
REGULAR_WEEKS = 14

CODES = ("best_team_name", "draft_day", "trade_of_year", "worst_beat")


def team_names(q, season):
    """Named in Song. The twelve names of that year, and whose they were."""
    rows = q("""
        select t.team_id, t.team_name, o.owner_id, o.username
        from teams t join owners o on o.owner_id = t.owner_id
        where t.season_year = %s
        order by t.team_name
    """, (season,))
    return [{"candidate": "name:%d" % r["team_id"],
             "owner_id": r["owner_id"],
             "detail": r["team_name"],
             "who": r["username"],
             "what": r["team_name"]} for r in rows]


def regular_season_complete(q, season):
    """Whether the regular season is complete: its last week scored.

    The same shape as week_is_complete in main, deliberately rewritten here
    rather than imported: main imports this module, so the arrow cannot go
    back the other way, and a ballot rule belongs beside the ballot it
    governs rather than in the caller that happens to draw it.
    """
    rows = q("""
        select count(*) as games,
               count(*) filter (where team_a_points is not null
                                 and (team_b_id is null
                                      or team_b_points is not null)) as scored
        from matchups where season_year = %s and week = %s
    """, (season, REGULAR_WEEKS))
    return bool(rows and rows[0]["games"]
                and rows[0]["games"] == rows[0]["scored"])


def late_picks(q, season):
    """Legend of the Choosing. The best pick of the late rounds.

    Three conditions, and each throws something out:

      round ten or after  -- the early rounds are where the good players
                             are, and taking one is not a discovery
      not a keeper        -- a keeper is not a pick, it is a price paid,
                             and it lands in whatever round it costs
      never moved, at any -- held the whole way, which is the claim being
      point in the season    made

    There was a fourth, on the end-of-season roster, and it was both
    redundant and costly. Redundant because a player nobody added, dropped
    or traded is still where the draft put him -- there is no other way off
    a roster. Costly because those rosters are loaded once the season is
    over and the assembly sits during the playoffs, so the ballot was empty
    exactly when the league was being asked to vote on it, and the admin
    page explained the emptiness by naming a load nobody could have done.

    Measured rather than argued before it came out: across 2022, 2023, 2024
    and 2025 the join changes nothing -- the same 12, 7, 3 and 7 candidates
    with it and without.

    What replaced it is a wait rather than a join. Nothing until the regular
    season is complete, which lands comfortably before the assembly sits. That keeps the honest half of the
    old rule -- this is a question about a whole season -- without asking
    for a file that does not exist yet.

    Why the surviving condition is the strong one, and not the same as
    "had him at the start and at the end". Borys drafted Luther Burden III in round
    thirteen, dropped him on the tenth of September, watched him pass through
    David, Curtis and David again, and picked him back up as a free agent on
    the twenty-seventh of December. Start and end both say Borys. Nothing in
    between does, and the crest is about the middle.

    Any transaction at all disqualifies: an add, a drop or a trade all mean
    somebody let him go or somebody else had him. Seven of 2025's nine
    survive it.
    """
    if not regular_season_complete(q, season):
        return []
    rows = q("""
        select o.owner_id, o.username, p.full_name, p.position,
               d.round, d.pick_in_round
        from draft_picks d
        join players p on p.player_id = d.player_id
        join teams t on t.team_id = d.team_id
        join owners o on o.owner_id = t.owner_id
        where d.season_year = %s and d.round >= %s and not d.is_keeper
          and not exists (
              select 1 from transactions x
              where x.season_year = d.season_year
                and x.player_id = d.player_id)
        order by d.round, d.pick_in_round
    """, (season, LATE_FROM))
    return [{"candidate": "pick:%d:%d" % (r["round"], r["pick_in_round"]),
             "owner_id": r["owner_id"],
             "detail": "%s, round %d" % (r["full_name"], r["round"]),
             "who": r["username"],
             "what": "%s, round %d" % (r["full_name"], r["round"]),
             "scores": r["position"]} for r in rows]


def trades(q, season):
    """The Bargain of the Age. Each side of each trade, with what it cost.

    Each side is its own candidate, because a bargain is something one of the
    two got and the vote should say which. And each says what was given as
    well as what was taken: "got A.J. Brown and Jaylen Warren" is not a deal,
    it is half of one, and nobody can judge it without the price.
    """
    rows = q("""
        select x.occurred_raw, x.occurred_on,
               to_timestamp(split_part(x.occurred_raw, ', ', 2),
                            'HH12:MI am')::time as at,
               ot.owner_id as to_owner, oto.username as to_who,
               ofr.owner_id as from_owner, ofrm.username as from_who,
               p.full_name
        from transactions x
        join players p on p.player_id = x.player_id
        join teams ot on ot.team_id = x.to_team_id
        join owners oto on oto.owner_id = ot.owner_id
        join teams ofr on ofr.team_id = x.from_team_id
        join owners ofrm on ofrm.owner_id = ofr.owner_id
        where x.season_year = %s and x.kind = 'trade'
        order by x.occurred_on, at, x.transaction_id
    """, (season,))

    # One row per player moved, so a three-for-one is four rows. A side is
    # everything one manager received on one date.
    sides = {}
    for r in rows:
        key = (r["occurred_raw"], r["to_owner"])
        side = sides.setdefault(key, {
            "candidate": "trade:%s:%d" % (r["occurred_raw"], r["to_owner"]),
            "owner_id": r["to_owner"], "who": r["to_who"],
            "from": r["from_who"], "got": [], "on": r["occurred_on"],
            "at": r["at"]})
        side["got"].append(r["full_name"])

    out = []
    # Earliest first, and by the time of day within a date: two trades on one
    # afternoon happened in an order, and a ballot that lists them the other
    # way round reads as though the second caused the first.
    for side in sorted(sides.values(),
                       key=lambda s: (s["on"] or dt.date.min,
                                      s["at"] or dt.time.min, s["who"])):
        got = _listed(side["got"])
        # What this manager gave is what the other one received, on the same
        # date and between the same two. A trade with only one side on record
        # still reads, it just cannot say the price.
        other = next((o for o in sides.values()
                      if o["on"] == side["on"] and o["who"] == side["from"]
                      and o["from"] == side["who"]), None)
        gave = _listed(other["got"]) if other else None
        what = ("got %s for %s" % (got, gave) if gave
                else "got %s from %s" % (got, side["from"]))
        out.append({"candidate": side["candidate"],
                    "owner_id": side["owner_id"],
                    "detail": what[4:] if gave else "%s from %s" % (got, side["from"]),
                    "who": side["who"],
                    "what": what,
                    # The two halves as lists, so a ballot can set them out a
                    # player to a line rather than in one run-on sentence.
                    "got": side["got"],
                    "gave": other["got"] if other else [],
                    "from": side["from"],
                    "scores": side["from"]})
    return out


def beats(q, season):
    """The Red Week. Every defeat by ten points or fewer, closest first.

    The crest belongs to the loser, so the loser is the candidate. The week
    and whether the two were rivals are part of the story -- losing by two to
    the manager you are set against all year is a different kind of bad
    afternoon from losing by two in week three.
    """
    rows = q("""
        select m.matchup_id, m.week, m.game_type,
               abs(m.team_a_points - m.team_b_points) as margin,
               case when m.team_a_points < m.team_b_points
                    then oa.owner_id else ob.owner_id end as loser_id,
               case when m.team_a_points < m.team_b_points
                    then oa.username else ob.username end as loser,
               case when m.team_a_points < m.team_b_points
                    then ob.username else oa.username end as winner,
               least(m.team_a_points, m.team_b_points) as lost_with,
               greatest(m.team_a_points, m.team_b_points) as beaten_by,
               (r.owner_id is not null) as rivals
        from matchups m
        join teams ta on ta.team_id = m.team_a_id
        join teams tb on tb.team_id = m.team_b_id
        join owners oa on oa.owner_id = ta.owner_id
        join owners ob on ob.owner_id = tb.owner_id
        left join rivalries r on r.season_year = m.season_year
             and r.owner_id = ta.owner_id and r.rival_owner_id = tb.owner_id
        where m.season_year = %s
          and m.team_a_points is not null and m.team_b_points is not null
          and abs(m.team_a_points - m.team_b_points) <= %s
        order by margin, m.week
    """, (season, CLOSE))

    out = []
    for r in rows:
        round_name = ("" if r["game_type"] == "regular"
                      else " " + r["game_type"].replace("_", " "))
        detail = ("lost to %s by %s in week %d%s"
                  % (r["winner"], _points(r["margin"]), r["week"], round_name))
        out.append({"candidate": "beat:%d" % r["matchup_id"],
                    "owner_id": r["loser_id"],
                    "detail": detail,
                    "who": r["loser"],
                    "what": detail,
                    "week": r["week"],
                    "margin": r["margin"],
                    "rivals": r["rivals"],
                    "scores": "%s to %s" % (_points(r["lost_with"]),
                                            _points(r["beaten_by"]))})
    return out


def ballots(q, season):
    """The four, in the order they are voted on."""
    return {"best_team_name": team_names(q, season),
            "draft_day": late_picks(q, season),
            "trade_of_year": trades(q, season),
            "worst_beat": beats(q, season)}


def _listed(names):
    """Two names joined by and, more by commas and an and."""
    if len(names) == 1:
        return names[0]
    return "%s and %s" % (", ".join(names[:-1]), names[-1])


def _points(n):
    """A score without the trailing zeroes nobody reads."""
    s = ("%.2f" % float(n)).rstrip("0").rstrip(".")
    return s or "0"

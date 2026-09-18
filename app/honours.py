"""The four honours the league votes on, and the ballots they are voted from.

Named in Song, Legend of the Choosing, The Bargain of the Age, The Red Week.
No rule computes them -- they are opinions -- so the league is asked.

Every candidate resolves to exactly one owner, because a crest is held by a
person. A team name belongs to its manager; a defeat belongs to whoever
suffered it; a late pick belongs to whoever made it; a trade has two sides and
each is its own candidate, so the vote decides who receives it rather than
leaving that to be settled afterwards.

Three of the four read a season that has been played. The fourth, the late
pick, also needs the end-of-season roster, because the condition that makes it
worth voting on is that the player was still there at the end.

Nothing here writes. It reads a season and returns the four ballots.
"""

# The margin under which a defeat is close enough to have been undeserved.
# Ten points is the league's own sense of it; the whole season is 102 games
# and around a quarter of them come in under this, which is a ballot rather
# than a list.
CLOSE = 10

# Where the late rounds begin. Ten of thirteen, so the last four.
LATE_FROM = 10

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


def late_picks(q, season):
    """Legend of the Choosing. The best pick of the late rounds.

    Three conditions, and each throws something out:

      round ten or after     -- the early rounds are where the good players
                                are, and taking one is not a discovery
      not a keeper           -- a keeper is not a pick, it is a price paid,
                                and it lands in whatever round it costs
      still on the roster at -- the difference between finding a player and
      the end of the season     taking a flier on one. Most late picks are
                                cut by October

    That leaves nine in 2025, which is a ballot. It needs the end-of-season
    roster, so a season with none offers nothing to vote on rather than
    offering a list nobody can judge.
    """
    rows = q("""
        select o.owner_id, o.username, p.full_name, p.position,
               d.round, d.pick_in_round
        from draft_picks d
        join players p on p.player_id = d.player_id
        join teams t on t.team_id = d.team_id
        join owners o on o.owner_id = t.owner_id
        join rosters r on r.season_year = d.season_year
                      and r.team_id = d.team_id
                      and r.player_id = d.player_id
        where d.season_year = %s and d.round >= %s and not d.is_keeper
        order by d.round, d.pick_in_round
    """, (season, LATE_FROM))
    return [{"candidate": "pick:%d:%d" % (r["round"], r["pick_in_round"]),
             "owner_id": r["owner_id"],
             "detail": "%s, round %d" % (r["full_name"], r["round"]),
             "who": r["username"],
             "what": "%s, round %d" % (r["full_name"], r["round"]),
             "scores": r["position"]} for r in rows]


def trades(q, season):
    """The Bargain of the Age. Each side of each trade, because a bargain is
    something one of the two got and the vote should say which."""
    rows = q("""
        select x.occurred_raw, x.occurred_on,
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
        order by x.occurred_on, x.transaction_id
    """, (season,))

    # One row per player moved, so a three-for-one is four rows. A side is
    # everything one manager received on one date.
    sides = {}
    for r in rows:
        key = (r["occurred_raw"], r["to_owner"])
        side = sides.setdefault(key, {
            "candidate": "trade:%s:%d" % (r["occurred_raw"], r["to_owner"]),
            "owner_id": r["to_owner"], "who": r["to_who"],
            "from": r["from_who"], "got": [], "on": r["occurred_on"]})
        side["got"].append(r["full_name"])

    out = []
    for side in sorted(sides.values(), key=lambda s: (s["on"] or 0, s["who"])):
        got = _listed(side["got"])
        out.append({"candidate": side["candidate"],
                    "owner_id": side["owner_id"],
                    "detail": "%s from %s" % (got, side["from"]),
                    "who": side["who"],
                    "what": "got %s from %s" % (got, side["from"])})
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

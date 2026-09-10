"""Compute the crests of honour.

Dry run by default, in the spirit of resolve_phase.py: it prints what it would
award and writes nothing. `--apply` writes.

    python scripts/award_crests.py                 # every season, dry run
    python scripts/award_crests.py --season 2026   # one season, dry run
    python scripts/award_crests.py --apply         # write

Auto crests are derived, so this is a rebuild rather than an append: the auto
rows for the seasons in scope are deleted and recomputed. Rows granted by a
commissioner -- award_mode 'manual', or any row with awarded_by set -- are
never touched.

Three shapes, and the difference is when the answer can be known:

  weekly   Settled by one week's scores and never revisited. Awarded per week,
           so a manager collects several across a season. Safe to compute the
           moment a week is entered.
  season   A superlative over a whole season: the best week of the year, the
           most points. Not awarded for a season still being played, because
           in week three it would name whoever happens to lead.
  career   Spans seasons. Every held crest is career-shaped and is recomputed
           on every run, including a single-season one, because a week's play
           can take one off its holder. Each is written twice over: once with
           no season, which is who holds it today, and once per finished
           season, which is who held it when that season closed.
"""

import argparse
import collections
import os

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

# --- thresholds, all in one place so they can be argued with ---------------
STREAK = 5              # consecutive results for The Unbroken / Winter Has Come
WHISKER = 1.00          # margin under which a game is won or lost by inches
ROBBED_BY = 20.0        # projected to win by this much, and lost
RIVAL_MEETINGS = 4      # minimum meetings before Bane of Their Rival counts
RECORD_SEASONS = 2      # minimum seasons before The Ever-Victorious counts
CAPTAIN_RUN = 2         # wins in a row to be Captain of the Kingsguard
FOOL_RUN = 3            # losses in a row to be The Court Fool
CONTRACTS_AT_ONCE = 3   # contracts held for Three Oaths Sworn


def q(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def leaders(rows, key):
    """Every row tied for the highest value, so a tie is shared rather than
    settled by whichever came back first."""
    if not rows:
        return []
    best = max(r[key] for r in rows)
    return [r for r in rows if r[key] == best]


def lowest(rows, key):
    """The mirror of leaders(), for a tiebreak where small is better."""
    if not rows:
        return []
    best = min(r[key] for r in rows)
    return [r for r in rows if r[key] == best]


# --- the rules ------------------------------------------------------------
#
# Each returns a list of (owner_id, season_year, week, detail, rank).
# season_year is None on a career crest; week is None on anything but a weekly
# one.
#
# `rank` decides which instance survives when a crest is earned twice in the
# same slot. Highest wins, and 0 where that cannot happen. Without it the
# database kept whichever row inserted first, which is to say an arbitrary one.


def pick_best(rows):
    """One row per manager per slot, the highest ranked. Returns the kept rows
    and the number dropped, so a silent collapse shows in the output."""
    best = {}
    for owner, year, week, detail, rank in rows:
        key = (owner, year, week)
        if key not in best or rank > best[key][4]:
            best[key] = (owner, year, week, detail, rank)
    return list(best.values()), len(rows) - len(best)


def complete_seasons(conn, seasons):
    done = {r["season_year"] for r in
            q(conn, "select season_year from season_results where is_complete")}
    return [s for s in seasons if s in done]


def silverware(conn, seasons):
    out = collections.defaultdict(list)
    for r in q(conn, """
        select season_year, champion_owner_id, runner_up_owner_id,
               third_owner_id, leader_owner_id, leader_wins, leader_losses
        from season_results where is_complete and season_year = any(%s)
    """, (seasons,)):
        y = r["season_year"]
        out["champion"].append((r["champion_owner_id"], y, None, None, 0))
        out["runner_up"].append((r["runner_up_owner_id"], y, None, None, 0))
        out["bronze"].append((r["third_owner_id"], y, None, None, 0))
        out["regular_season_crown"].append(
            (r["leader_owner_id"], y, None,
             f"{r['leader_wins']}-{r['leader_losses']}", 0))

    for r in q(conn, """
        select s.owner_id, s.season_year from team_season_stats s
        join season_results sr on sr.season_year = s.season_year
        where sr.is_complete and s.final_rank = 12 and s.season_year = any(%s)
    """, (seasons,)):
        out["sacko"].append((r["owner_id"], r["season_year"], None, None, 0))

    wins = collections.defaultdict(list)
    for r in q(conn, "select season_year, champion_owner_id from season_results "
                     "where is_complete order by season_year"):
        wins[r["champion_owner_id"]].append(r["season_year"])
    for oid, years in wins.items():
        pairs = [(a, b) for a, b in zip(years, years[1:]) if b == a + 1]
        if pairs:
            out["dynasty"].append(
                (oid, None, None, ", ".join(f"{a} and {b}" for a, b in pairs), 0))
    return out


def weekly(conn, seasons):
    """Crests a single week settles, and nothing later can change.

    These do not wait for a season to finish. Whoever scored most in week three
    scored most in week three whatever happens afterwards, so the crest is
    awarded as soon as the scores are in, including mid-season.
    """
    out = collections.defaultdict(list)
    for y in seasons:
        games = q(conn, """
            select g.owner_id, g.week, g.points_for, g.points_against, g.result,
                   case when m.team_a_id = g.team_id then m.team_a_projected
                        else m.team_b_projected end as projected,
                   case when m.team_a_id = g.team_id then m.team_b_projected
                        else m.team_a_projected end as opp_projected
            from game_log g
            join matchups m on m.matchup_id = g.matchup_id
            where g.season_year = %s and g.game_type = 'regular'
        """, (y,))
        if not games:
            continue

        by_week = collections.defaultdict(list)
        for g in games:
            by_week[g["week"]].append(g)

        for wk, played in by_week.items():
            for g in leaders(played, "points_for"):
                out["week_high"].append(
                    (g["owner_id"], y, wk, f"{g['points_for']:.2f}", 0))

            won = [dict(g, margin=g["points_for"] - g["points_against"])
                   for g in played if g["result"] == "W"]
            for g in leaders(won, "margin"):
                out["week_rout"].append(
                    (g["owner_id"], y, wk, f"won by {g['margin']:.2f}", 0))

            for g in won:
                if g["margin"] < WHISKER:
                    out["nailbiter"].append(
                        (g["owner_id"], y, wk, f"won by {g['margin']:.2f}", 0))
                # Won while falling short of their own projection.
                if g["projected"] is not None and g["points_for"] < g["projected"]:
                    out["lucky_win"].append(
                        (g["owner_id"], y, wk,
                         f"{g['projected'] - g['points_for']:.1f} under projection", 0))

            for g in played:
                if g["result"] == "W" or g["projected"] is None:
                    continue
                edge = g["projected"] - g["opp_projected"]
                if edge >= ROBBED_BY:
                    out["robbed"].append(
                        (g["owner_id"], y, wk, f"favoured by {edge:.1f}", 0))

        # A streak lands in the week its fifth result arrives. A longer run
        # does not award again: seven straight is one streak, not three.
        by_owner = collections.defaultdict(list)
        for g in sorted(games, key=lambda g: g["week"]):
            by_owner[g["owner_id"]].append(g)
        for oid, gs in by_owner.items():
            for code, want in (("streak_five", "W"), ("losing_streak", "L")):
                run = 0
                for g in gs:
                    run = run + 1 if g["result"] == want else 0
                    if run == STREAK:
                        out[code].append(
                            (oid, y, g["week"],
                             f"weeks {g['week'] - STREAK + 1}–{g['week']}", 0))
    return out


def season_long(conn, seasons):
    """Superlatives over a whole season, and only a finished one.

    Most points, the best week of the year, the biggest rout of the year: none
    of these can be known in week three, and awarding them to whoever leads at
    the time would name the wrong manager for most of a season.
    """
    out = collections.defaultdict(list)
    for y in complete_seasons(conn, seasons):
        games = q(conn, """
            select owner_id, week, points_for, points_against,
                   opponent_owner_id, result
            from game_log where season_year = %s and game_type = 'regular'
        """, (y,))
        if not games:
            continue

        for g in leaders(games, "points_for"):
            out["weekly_high"].append(
                (g["owner_id"], y, None,
                 f"{g['points_for']:.2f}, week {g['week']}", 0))

        won = [dict(g, margin=g["points_for"] - g["points_against"])
               for g in games if g["result"] == "W"]
        for g in leaders(won, "margin"):
            out["blowout"].append(
                (g["owner_id"], y, None,
                 f"{g['margin']:.2f}, week {g['week']}", 0))

        totals = q(conn, """
            select owner_id, points_for, points_against
            from team_season_stats where season_year = %s and games_played > 0
        """, (y,))
        for t in leaders(totals, "points_for"):
            out["season_high_points"].append(
                (t["owner_id"], y, None, f"{t['points_for']:.1f}", 0))
        for t in leaders(totals, "points_against"):
            out["points_against_king"].append(
                (t["owner_id"], y, None, f"{t['points_against']:.1f} conceded", 0))

        by_owner = collections.defaultdict(list)
        for g in games:
            by_owner[g["owner_id"]].append(g)
        for oid, gs in by_owner.items():
            faced = {g["opponent_owner_id"] for g in gs}
            beaten = {g["opponent_owner_id"] for g in gs if g["result"] == "W"}
            if faced and beaten == faced:
                out["gauntlet"].append(
                    (oid, y, None, f"beat all {len(faced)} faced", 0))

        for r in leaders(q(conn, """
            select t.owner_id,
                   sum(case when m.team_a_id = t.team_id
                            then m.team_a_points - m.team_a_projected
                            else m.team_b_points - m.team_b_projected end) as over
            from matchups m
            join teams t on t.team_id in (m.team_a_id, m.team_b_id)
            where m.season_year = %s and m.game_type = 'regular'
              and m.team_a_projected is not null and m.team_a_points is not null
              and t.season_year = %s
            group by t.owner_id
        """, (y, y)), "over"):
            out["overachiever"].append(
                (r["owner_id"], y, None, f"{r['over']:+.1f} over the season", 0))
    return out


def keepers(conn, seasons):
    out = collections.defaultdict(list)
    for r in q(conn, """
        select t.owner_id, ks.season_year, count(*) as n
        from keeper_selections ks join teams t on t.team_id = ks.team_id
        where ks.contract_id is not null and ks.season_year = any(%s)
        group by t.owner_id, ks.season_year
    """, (seasons,)):
        if r["n"] >= CONTRACTS_AT_ONCE:
            out["triple_threat"].append(
                (r["owner_id"], r["season_year"], None, f"{r['n']} contracts", 0))

    # Counted, not one row per void: several voids in a season are allowed and
    # the crest is one per manager per season.
    for r in q(conn, """
        select owner_id, season_year, count(*) as n, min(penalty_round) as lowest
        from keeper_voids where season_year = any(%s)
        group by owner_id, season_year
    """, (seasons,)):
        detail = (f"{r['n']} voids, first defence pick at round {r['lowest']}"
                  if r["n"] > 1 else f"defence pick at round {r['lowest']}")
        out["cut_bait"].append((r["owner_id"], r["season_year"], None, detail, 0))
    return out


def transactions(conn, seasons):
    """Only for seasons that have transaction rows at all.

    2022 to 2024 have no transaction history; they do not have a quiet one.
    """
    out = collections.defaultdict(list)
    have = {r["season_year"] for r in
            q(conn, "select distinct season_year from transactions")}
    for y in [s for s in complete_seasons(conn, seasons) if s in have]:
        for r in leaders(q(conn, """
            select t.owner_id, count(*) as n
            from transactions x join teams t on t.team_id = x.to_team_id
            where x.season_year = %s and x.kind = 'add' and t.season_year = %s
            group by t.owner_id
        """, (y, y)), "n"):
            out["waiver_warrior"].append(
                (r["owner_id"], y, None, f"{r['n']} adds", 0))

        # One transaction row per player moved, so a three-for-one is four rows
        # and counting rows would call it four trades. A trade is one date and
        # one pair of teams; count those.
        for r in leaders(q(conn, """
            select t.owner_id, count(distinct (
                       x.occurred_on,
                       least(x.from_team_id, x.to_team_id),
                       greatest(x.from_team_id, x.to_team_id))) as n
            from transactions x join teams t
              on t.team_id in (x.to_team_id, x.from_team_id)
            where x.season_year = %s and x.kind = 'trade' and t.season_year = %s
            group by t.owner_id
        """, (y, y)), "n"):
            out["wheeler_dealer"].append(
                (r["owner_id"], y, None, f"{r['n']} trades", 0))
    return out


def rivalry(conn, seasons):
    """The highest score of rivalry week.

    Rivalry week is read off the fixtures rather than pinned to week 10: a week
    qualifies when every game in it is a meeting between rivals. Across every
    season so far that is true of exactly one week, 2026's tenth, which has not
    been played -- so this awards nothing yet, by design.
    """
    out = collections.defaultdict(list)
    for y in seasons:
        pairs = {(r["a"], r["b"]) for r in q(conn, """
            select owner_id as a, rival_owner_id as b from rivalries
            where season_year = %s
        """, (y,))}
        if not pairs:
            continue
        weeks = collections.defaultdict(list)
        for g in q(conn, """
            select m.week, ta.owner_id as a, tb.owner_id as b,
                   m.team_a_points as pa, m.team_b_points as pb
            from matchups m
            join teams ta on ta.team_id = m.team_a_id
            join teams tb on tb.team_id = m.team_b_id
            where m.season_year = %s and m.game_type = 'regular'
        """, (y,)):
            weeks[g["week"]].append(g)
        for wk, games in weeks.items():
            if len(games) < 2 or not all((g["a"], g["b"]) in pairs for g in games):
                continue
            scores = []
            for g in games:
                if g["pa"] is None or g["pb"] is None:
                    scores = []
                    break
                scores += [{"owner_id": g["a"], "pts": g["pa"]},
                           {"owner_id": g["b"], "pts": g["pb"]}]
            for s in leaders(scores, "pts"):
                out["rivalry_week_high"].append(
                    (s["owner_id"], y, None, f"{s['pts']:.2f}, week {wk}", 0))
    return out


# Season-and-week, as one comparison. Held crests move at different speeds
# and this is the cut that suits all of them.
CUT = "(g.season_year < %s or (g.season_year = %s and g.week <= %s))"


def held(conn, season=None, week=None):
    """The eight crests only one manager holds at a time.

    Recomputed on every run, including a single-season one: a week's play can
    take one of these off its holder, and a weekly recompute that skipped them
    would leave the rings wrong until someone remembered to run a full one.

    The cut is a season *and* a week, because these titles move at very
    different speeds. The throne changes when a season ends. Captain of the
    Kingsguard can change every week, and recording it once a year recorded
    almost none of it -- in 2025 it passed through five managers and the
    season close caught the fifth. So:

        season None            who holds each now
        season Y, week None    who held each when Y closed
        season Y, week W       who held each after week W of Y

    All three are stored. The season pages ring their sigils from the middle
    one, and the week rows are the history of how a title actually moved.

    The tallies count from game_log rather than from owner_crests rows.
    Counting rows would make one crest depend on another having been computed
    first, and a rebuild would then give different answers depending on the
    order it happened to run in.
    """
    out = collections.defaultdict(list)
    cut_y = 9999 if season is None else season
    cut_w = 99 if week is None else week
    cut = (cut_y, cut_y, cut_w)
    mid = week is not None

    # A season still being played has not produced a champion, so mid-season
    # the reigning one is still the last finished season's.
    done = q(conn, "select max(season_year) as y from season_results "
                   "where is_complete and season_year <= %s",
             (cut_y - 1 if mid else cut_y,))[0]["y"]
    if done:
        for r in q(conn, "select champion_owner_id as o from season_results "
                         "where season_year = %s", (done,)):
            out["reigning_champion"].append(
                (r["o"], season, week, f"champion of {done}", 0))
        for r in q(conn, "select owner_id as o from team_season_stats "
                         "where season_year = %s and final_rank = 12", (done,)):
            out["reigning_sacko"].append(
                (r["o"], season, week, f"last in {done}", 0))

    # Best win rate in league history. Two seasons minimum: a newcomer at 10-4
    # would otherwise hold it off fourteen games. Counted from game_log rather
    # than read from team_season_stats, which is a season at a time and cannot
    # answer what a record was in week nine.
    best = []
    for r in q(conn, f"""
        select owner_id,
               count(*) filter (where result = 'W') as wins,
               count(*) filter (where result = 'L') as losses,
               count(*) filter (where result = 'T') as ties,
               sum(points_for) as points_for,
               count(distinct season_year) as seasons
        from game_log g
        where game_type = 'regular' and {CUT}
        group by owner_id
    """, cut):
        played = r["wins"] + r["losses"] + r["ties"]
        if r["seasons"] >= RECORD_SEASONS and played:
            r["win_pct"] = (r["wins"] + r["ties"] * 0.5) / played
            best.append(r)
    # A tie on rate goes to the most points scored. Records repeat -- 2023
    # closed with three managers on 18-10 apiece -- and a crest only one
    # manager holds cannot be settled by the thing they are tied on. Points
    # for is the league's own tiebreak everywhere else it needs one.
    for r in leaders(leaders(best, "win_pct"), "points_for"):
        out["best_record"].append(
            (r["owner_id"], season, week,
             f"{r['wins']}-{r['losses']}, {r['win_pct']:.3f}", 0))

    # Each manager's newest rival as at the cut, then their record against
    # that person over every meeting up to it.
    #
    # Rate first, then meetings, then points scored in those meetings. A
    # perfect record is easy to tie -- 2023 closed with Joey and Tom both 4-0
    # over four games -- and this is a crest about one rivalry, so the last
    # word goes to how they scored inside it rather than to a career total
    # made up mostly of games against everybody else.
    h2h = {(r["owner_id"], r["opponent_owner_id"]): r for r in q(conn, f"""
        select owner_id, opponent_owner_id, count(*) as games,
               count(*) filter (where result = 'W') as wins,
               count(*) filter (where result = 'L') as losses,
               sum(points_for) as points_for
        from game_log g where {CUT}
        group by owner_id, opponent_owner_id
    """, cut)}
    rivals = []
    for r in q(conn, """
        select distinct on (r.owner_id) r.owner_id, r.rival_owner_id
        from rivalries r where r.season_year <= %s
        order by r.owner_id, r.season_year desc
    """, (cut_y,)):
        rec = h2h.get((r["owner_id"], r["rival_owner_id"]))
        if rec and rec["games"] >= RIVAL_MEETINGS:
            rivals.append({"owner_id": r["owner_id"], "games": rec["games"],
                           "rate": rec["wins"] / rec["games"],
                           "points_for": rec["points_for"],
                           "wins": rec["wins"], "losses": rec["losses"]})
    for r in leaders(leaders(leaders(rivals, "rate"), "games"), "points_for"):
        out["fiercest_rival"].append(
            (r["owner_id"], season, week,
             f"{r['wins']}-{r['losses']} against their rival", 0))

    # Most weeks topping the league's scoring, a tie going to the most points
    # scored. Two managers on three weeks apiece was how 2022 closed, and a
    # count that low ties easily.
    #
    # The filter counts the top weeks while the sum runs over every regular
    # season game, which is why the rank is computed in the subquery and
    # tested in the outer one rather than filtered there.
    for r in leaders(leaders(q(conn, f"""
        select owner_id,
               count(*) filter (where rk = 1) as n,
               sum(points_for) as points_for
        from (
            select owner_id, points_for, rank() over (
                       partition by season_year, week order by points_for desc) as rk
            from game_log g
            where game_type = 'regular' and {CUT}) t
        group by owner_id
    """, cut), "n"), "points_for"):
        out["most_weekly_highs"].append(
            (r["owner_id"], season, week, f"{r['n']} weeks", 0))

    # The two streak titles. A streak runs across a season boundary: win
    # your last three of one year and your first two of the next and that is
    # five, not two.
    #
    # Which is why a retired manager is exempt. Nothing ever ends their run,
    # so Theo -- who finished 2023 on two wins and has not played since --
    # would have held the Captaincy through parts of 2025 without kicking a
    # ball. The exemption is on the title, not on the streak: his games still
    # count for everyone else's.
    #
    # Regular season only, like every other streak rule here: a playoff run
    # is a different thing and the bracket already says who went furthest.
    #
    # Both can sit vacant, which none of the other titles can. Below the
    # minimum nobody holds it, because a title that always belongs to
    # somebody says nothing when the best run going is one game.
    retired = {r["owner_id"] for r in
               q(conn, "select owner_id from owners where is_retired")}
    won = collections.defaultdict(lambda: (0, 0.0))
    lost = collections.defaultdict(lambda: (0, 0.0))
    for r in q(conn, f"""
        select owner_id, result, points_for from game_log g
        where game_type = 'regular' and {CUT}
        order by owner_id, season_year, week
    """, cut):
        pts = float(r["points_for"])
        w, wp = won[r["owner_id"]]
        l, lp = lost[r["owner_id"]]
        won[r["owner_id"]] = (w + 1, wp + pts) if r["result"] == "W" else (0, 0.0)
        lost[r["owner_id"]] = (l + 1, lp + pts) if r["result"] == "L" else (0, 0.0)

    runs = [{"owner_id": o, "n": n, "pts": p}
            for o, (n, p) in won.items() if n >= CAPTAIN_RUN and o not in retired]
    # The tiebreak is the points scored inside the streak, not a career
    # total: two managers on four straight is likely, and the crest is about
    # the run, so the run decides it.
    for r in leaders(leaders(runs, "n"), "pts"):
        out["kingsguard"].append(
            (r["owner_id"], season, week,
             f"{r['n']} straight, {r['pts']:.1f} scored", 0))

    # The Fool is the mirror, and so is its tiebreak: fewest points scored,
    # not most. It is the one crest here where less is worse.
    runs = [{"owner_id": o, "n": n, "pts": p}
            for o, (n, p) in lost.items() if n >= FOOL_RUN and o not in retired]
    for r in lowest(leaders(runs, "n"), "pts"):
        out["court_fool"].append(
            (r["owner_id"], season, week,
             f"{r['n']} straight, {r['pts']:.1f} scored", 0))

    # Most games won, and lost, by under a point. Both come out as four-way
    # ties on two apiece, so a count alone cannot decide a crest only one
    # manager holds. The tiebreak is the margins added together, smallest
    # winning: two wins by 0.34 and 0.38 is a closer run than two by 0.23 and
    # 0.75, even though the second contains the tighter single game.
    for code, result, margin in (
            ("most_narrow_wins", "W", "points_for - points_against"),
            ("most_narrow_losses", "L", "points_against - points_for")):
        rows = q(conn, f"""
            select owner_id, count(*) as n, sum({margin}) as total
            from game_log g
            where game_type = 'regular' and {CUT}
              and result = %s and {margin} < %s
            group by owner_id
        """, cut + (result, WHISKER))
        for r in lowest(leaders(rows, "n"), "total"):
            out[code].append(
                (r["owner_id"], season, week,
                 f"{r['n']}, {r['total']:.2f} between them", 0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, help="one season; default is all")
    ap.add_argument("--apply", action="store_true", help="write, rather than print")
    args = ap.parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        years = [r["season_year"] for r in
                 q(conn, "select season_year from seasons order by season_year")]
        seasons = [args.season] if args.season else years

        crests = {r["code"]: r for r in q(conn, "select * from crests")}
        awards = collections.defaultdict(list)
        for rule in (silverware, weekly, season_long, keepers,
                     transactions, rivalry):
            for code, rows in rule(conn, seasons).items():
                awards[code] += rows
        # Who holds each title now, and who held it at the close of every
        # finished season in scope. The second lot is what lets a season page
        # ring its sigils with that year's titles rather than today's.
        for code, rows in held(conn).items():
            awards[code] += rows
        for y in complete_seasons(conn, seasons):
            for code, rows in held(conn, y).items():
                awards[code] += rows
            # And after every week of it. A title that changes hands in week
            # nine and back in week ten leaves no trace in a season-close
            # snapshot, which is most of what these titles do.
            # Every week that was played, playoffs included. Bane of Their
            # Rival counts every meeting, and Tom's fourth against David was
            # the 2023 semifinal -- stopping at week 14 lost the only spell
            # he ever had of it. The streak rules read regular games only and
            # are unmoved by the extra weeks.
            last = q(conn, """
                select max(week) as w from game_log where season_year = %s
            """, (y,))[0]["w"] or 0
            for w in range(1, last + 1):
                for code, rows in held(conn, y, w).items():
                    awards[code] += rows

        dropped = 0
        for code in list(awards):
            kept, lost = pick_best([r for r in awards[code] if r[0] is not None])
            awards[code] = kept
            dropped += lost

        names = {r["owner_id"]: r["username"] for r in
                 q(conn, "select owner_id, username from owners")}

        total = 0
        for code in sorted(awards, key=lambda c: crests[c]["sort_order"]):
            rows = awards[code]
            if not rows:
                continue
            total += len(rows)
            c = crests[code]
            print(f"\n{c['name']}  ({code}, {c['category']}, {c['standing']})")
            shown = sorted(rows, key=lambda r: (r[1] or 0, r[2] or 0, names[r[0]]))
            for oid, year, week, detail, _ in shown[:8]:
                when = (f"{year} wk{week}" if week
                        else (str(year) if year else "career"))
                print("   %-8s %-10s %s" % (names[oid], when, detail or ""))
            if len(shown) > 8:
                print("   ... and %d more" % (len(shown) - 8))

        silent = [c for c, r in crests.items()
                  if r["award_mode"] == "auto" and not awards.get(c)]
        print("\n%d awards across %d crests."
              % (total, len({c for c in awards if awards[c]})))
        if dropped:
            print("%d further qualifying events folded into a crest already "
                  "held in the same slot." % dropped)
        if silent:
            print("Awarded to nobody: " + ", ".join(sorted(silent)))

        if not args.apply:
            print("\nDry run. Nothing written. Pass --apply to write.")
            return

        with conn.cursor() as cur:
            # A season-scoped run still has to clear the live held rows or
            # the rings go stale -- but only those and that season's own. The
            # old clause took every held row, which would now delete the
            # other seasons' snapshots without computing replacements.
            if args.season:
                cur.execute("""
                    delete from owner_crests oc using crests c
                     where c.crest_id = oc.crest_id and c.award_mode = 'auto'
                       and oc.awarded_by is null
                       and (oc.season_year = %s
                            or (c.standing = 'held' and oc.season_year is null))
                """, (args.season,))
            else:
                cur.execute("""
                    delete from owner_crests oc using crests c
                     where c.crest_id = oc.crest_id and c.award_mode = 'auto'
                       and oc.awarded_by is null
                """)
            removed = cur.rowcount
            written = 0
            for code, rows in awards.items():
                cid = crests[code]["crest_id"]
                for oid, year, week, detail, _ in rows:
                    cur.execute("""
                        insert into owner_crests
                            (owner_id, crest_id, season_year, week, detail)
                        values (%s, %s, %s, %s, %s)
                        on conflict do nothing
                    """, (oid, cid, year, week, detail))
                    written += cur.rowcount
        conn.commit()
        print(f"\nRemoved {removed} auto rows, wrote {written}.")


if __name__ == "__main__":
    main()

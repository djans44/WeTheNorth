"""The league table as it stood at the end of a given week.

Two callers want this and they sit in different layers -- the season page
draws it, and a weekly summary is written from it -- so the query lives here
rather than in either of them.

team_season_stats cannot answer it: it covers a whole season, so on a finished
year it would put the final order underneath week three. This counts from the
scores instead. Regular games only, and an unplayed week contributes nothing,
so the table simply stops where the season has got to.

final_rank comes back as a null so these rows have the same shape as the
season-wide ones and one template block can draw either. The missing rank is
deliberate: a position mid-season is the row's place in this order, not a
settled finish.

Parameters are named -- %(y)s for the season, %(w)s for the week -- because
both appear twice and positional ones would have to be passed four times.
"""

TABLE_AFTER_WEEK = """
    with sides as (
        select m.team_a_id as team_id, m.team_a_points as pf,
               m.team_b_points as pa
        from matchups m
        where m.season_year = %(y)s and m.game_type = 'regular'
          and m.week <= %(w)s
          and m.team_a_points is not null and m.team_b_points is not null
        union all
        select m.team_b_id, m.team_b_points, m.team_a_points
        from matchups m
        where m.season_year = %(y)s and m.game_type = 'regular'
          and m.week <= %(w)s and m.team_b_id is not null
          and m.team_a_points is not null and m.team_b_points is not null
    )
    select t.team_id, t.team_name, o.username,
           null::int as final_rank,
           count(s.team_id) filter (where s.pf > s.pa)  as wins,
           count(s.team_id) filter (where s.pf < s.pa)  as losses,
           count(s.team_id) filter (where s.pf = s.pa)  as ties,
           coalesce(sum(s.pf), 0) as points_for,
           coalesce(sum(s.pa), 0) as points_against
    from teams t
    join owners o on o.owner_id = t.owner_id
    left join sides s on s.team_id = t.team_id
    where t.season_year = %(y)s
    group by t.team_id, t.team_name, o.username
    order by count(s.team_id) filter (where s.pf > s.pa)
             + 0.5 * count(s.team_id) filter (where s.pf = s.pa) desc,
             coalesce(sum(s.pf), 0) desc
"""

drop view if exists owner_all_time_stats;
drop view if exists team_season_stats;

-- Convenience: every completed game, one row per team, with names attached.
create view game_log as
select
    r.matchup_id,
    r.season_year,
    r.week,
    r.game_type,
    r.team_id,
    t.team_name,
    t.owner_id,
    o.username,
    r.opponent_team_id,
    ot.team_name as opponent_team_name,
    ot.owner_id  as opponent_owner_id,
    oo.username  as opponent_username,
    r.points_for,
    r.points_against,
    r.result
from team_game_results r
join teams  t  on t.team_id  = r.team_id
join owners o  on o.owner_id = t.owner_id
join teams  ot on ot.team_id = r.opponent_team_id
join owners oo on oo.owner_id = ot.owner_id;


-- Rebuilt to distinguish the championship bracket from consolation.
create view team_season_stats as
select
    t.team_id,
    t.season_year,
    t.owner_id,
    o.username,
    t.team_name,
    count(r.matchup_id) filter (where r.game_type = 'regular')            as games_played,
    count(*) filter (where r.game_type = 'regular' and r.result = 'W')    as wins,
    count(*) filter (where r.game_type = 'regular' and r.result = 'L')    as losses,
    count(*) filter (where r.game_type = 'regular' and r.result = 'T')    as ties,
    coalesce(sum(r.points_for)     filter (where r.game_type = 'regular'), 0) as points_for,
    coalesce(sum(r.points_against) filter (where r.game_type = 'regular'), 0) as points_against,
    coalesce(bool_or(r.game_type in
        ('quarterfinal', 'semifinal', 'championship', 'third_place')), false) as made_playoffs
from teams t
join owners o on o.owner_id = t.owner_id
left join team_game_results r on r.team_id = t.team_id
group by t.team_id, t.season_year, t.owner_id, o.username, t.team_name;


-- Champion, runner-up, third, and best regular season, one row per season.
create view season_results as
with champ as (
    select
        m.season_year,
        case when m.team_a_points > m.team_b_points then m.team_a_id else m.team_b_id end as champion_team_id,
        case when m.team_a_points > m.team_b_points then m.team_b_id else m.team_a_id end as runner_up_team_id
    from matchups m
    where m.game_type = 'championship'
      and m.team_a_points is not null
      and m.team_b_points is not null
),
third as (
    select
        m.season_year,
        case when m.team_a_points > m.team_b_points then m.team_a_id else m.team_b_id end as third_team_id
    from matchups m
    where m.game_type = 'third_place'
      and m.team_a_points is not null
      and m.team_b_points is not null
),
leader as (
    select distinct on (season_year) season_year, team_id, wins, losses
    from team_season_stats
    where games_played > 0
    order by season_year, wins desc, points_for desc
)
select
    s.season_year,
    s.is_complete,
    ct.owner_id   as champion_owner_id,
    co.username   as champion,
    ct.team_name  as champion_team,
    rt.owner_id   as runner_up_owner_id,
    ro.username   as runner_up,
    rt.team_name  as runner_up_team,
    ht.owner_id   as third_owner_id,
    ho.username   as third_place,
    ht.team_name  as third_place_team,
    lt.owner_id   as leader_owner_id,
    lo.username   as regular_season_leader,
    lt.team_name  as regular_season_leader_team,
    l.wins        as leader_wins,
    l.losses      as leader_losses
from seasons s
left join champ  c  on c.season_year  = s.season_year
left join third  th on th.season_year = s.season_year
left join leader l  on l.season_year  = s.season_year
left join teams  ct on ct.team_id = c.champion_team_id
left join teams  rt on rt.team_id = c.runner_up_team_id
left join teams  ht on ht.team_id = th.third_team_id
left join teams  lt on lt.team_id = l.team_id
left join owners co on co.owner_id = ct.owner_id
left join owners ro on ro.owner_id = rt.owner_id
left join owners ho on ho.owner_id = ht.owner_id
left join owners lo on lo.owner_id = lt.owner_id;


-- All-time record per owner, with titles and playoff appearances.
create view owner_all_time_stats as
select
    s.owner_id,
    s.username,
    count(*) filter (where s.games_played > 0)      as seasons_played,
    sum(s.wins)                                     as wins,
    sum(s.losses)                                   as losses,
    sum(s.ties)                                     as ties,
    sum(s.points_for)                               as points_for,
    sum(s.points_against)                           as points_against,
    round(sum(s.points_for) / nullif(sum(s.games_played), 0), 2) as points_per_game,
    round(
        (sum(s.wins) + sum(s.ties) * 0.5)
        / nullif(sum(s.wins) + sum(s.losses) + sum(s.ties), 0), 3
    )                                               as win_pct,
    count(*) filter (where s.made_playoffs)         as playoff_appearances,
    (select count(*) from season_results sr where sr.champion_owner_id = s.owner_id) as titles,
    (select count(*) from season_results sr where sr.runner_up_owner_id = s.owner_id) as runner_ups
from team_season_stats s
group by s.owner_id, s.username;


-- All-time record of every owner against every other owner.
create view owner_head_to_head as
select
    g.owner_id,
    g.username,
    g.opponent_owner_id,
    g.opponent_username,
    count(*)                                as games,
    count(*) filter (where g.result = 'W')  as wins,
    count(*) filter (where g.result = 'L')  as losses,
    count(*) filter (where g.result = 'T')  as ties,
    sum(g.points_for)                       as points_for,
    sum(g.points_against)                   as points_against
from game_log g
group by g.owner_id, g.username, g.opponent_owner_id, g.opponent_username;


-- Actual scoring versus Yahoo projection, regular season only.
create view owner_projection_stats as
with sides as (
    select m.season_year, m.game_type, m.team_a_id as team_id,
           m.team_a_points as points, m.team_a_projected as projected
    from matchups m
    where m.team_b_id is not null
      and m.team_a_points is not null
      and m.team_a_projected is not null
    union all
    select m.season_year, m.game_type, m.team_b_id,
           m.team_b_points, m.team_b_projected
    from matchups m
    where m.team_b_id is not null
      and m.team_b_points is not null
      and m.team_b_projected is not null
)
select
    t.owner_id,
    o.username,
    count(*)                                            as games,
    round(avg(s.points - s.projected), 2)               as avg_vs_projection,
    round(sum(s.points - s.projected), 2)               as total_vs_projection,
    count(*) filter (where s.points > s.projected)      as games_over_projection
from sides s
join teams  t on t.team_id  = s.team_id
join owners o on o.owner_id = t.owner_id
where s.game_type = 'regular'
group by t.owner_id, o.username;

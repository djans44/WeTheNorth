-- One row per team per completed game, both sides of every matchup.
create view team_game_results as
select
    m.matchup_id,
    m.season_year,
    m.week,
    m.game_type,
    m.team_a_id as team_id,
    m.team_b_id as opponent_team_id,
    m.team_a_points as points_for,
    m.team_b_points as points_against,
    case
        when m.team_a_points > m.team_b_points then 'W'
        when m.team_a_points < m.team_b_points then 'L'
        else 'T'
    end as result
from matchups m
where m.team_a_points is not null
  and m.team_b_points is not null
union all
select
    m.matchup_id,
    m.season_year,
    m.week,
    m.game_type,
    m.team_b_id,
    m.team_a_id,
    m.team_b_points,
    m.team_a_points,
    case
        when m.team_b_points > m.team_a_points then 'W'
        when m.team_b_points < m.team_a_points then 'L'
        else 'T'
    end
from matchups m
where m.team_a_points is not null
  and m.team_b_points is not null;


-- Regular season record and scoring, one row per team per season.
create view team_season_stats as
select
    t.team_id,
    t.season_year,
    t.owner_id,
    o.username,
    t.team_name,
    count(r.matchup_id) filter (where r.game_type = 'regular')             as games_played,
    count(*) filter (where r.game_type = 'regular' and r.result = 'W')     as wins,
    count(*) filter (where r.game_type = 'regular' and r.result = 'L')     as losses,
    count(*) filter (where r.game_type = 'regular' and r.result = 'T')     as ties,
    coalesce(sum(r.points_for)     filter (where r.game_type = 'regular'), 0) as points_for,
    coalesce(sum(r.points_against) filter (where r.game_type = 'regular'), 0) as points_against,
    count(*) filter (where r.game_type <> 'regular')                       as playoff_games
from teams t
join owners o on o.owner_id = t.owner_id
left join team_game_results r on r.team_id = t.team_id
group by t.team_id, t.season_year, t.owner_id, o.username, t.team_name;


-- All-time totals, one row per owner.
create view owner_all_time_stats as
select
    s.owner_id,
    s.username,
    count(*)                    as seasons_played,
    sum(s.wins)                 as wins,
    sum(s.losses)               as losses,
    sum(s.ties)                 as ties,
    sum(s.points_for)           as points_for,
    sum(s.points_against)       as points_against,
    round(sum(s.points_for) / nullif(sum(s.games_played), 0), 2) as points_per_game
from team_season_stats s
group by s.owner_id, s.username;

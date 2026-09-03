alter table matchups drop constraint matchups_game_type_check;

alter table matchups add constraint matchups_game_type_check
    check (game_type in (
        'regular', 'quarterfinal', 'semifinal', 'championship',
        'third_place', 'fifth_place', 'seventh_place',
        'ninth_place', 'eleventh_place', 'consolation'
    ));

-- Week 16: the two quarterfinal losers are playing for fifth.
update matchups m set game_type = 'fifth_place'
where m.week = 16 and m.game_type = 'consolation'
  and exists (select 1 from team_game_results r
              where r.team_id = m.team_a_id and r.week = 15
                and r.game_type = 'quarterfinal' and r.result = 'L')
  and exists (select 1 from team_game_results r
              where r.team_id = m.team_b_id and r.week = 15
                and r.game_type = 'quarterfinal' and r.result = 'L');

-- Week 16: the two week-15 consolation losers are playing for eleventh.
update matchups m set game_type = 'eleventh_place'
where m.week = 16 and m.game_type = 'consolation'
  and exists (select 1 from team_game_results r
              where r.team_id = m.team_a_id and r.week = 15
                and r.game_type = 'consolation' and r.result = 'L')
  and exists (select 1 from team_game_results r
              where r.team_id = m.team_b_id and r.week = 15
                and r.game_type = 'consolation' and r.result = 'L');

-- Week 17: winners of the week-16 consolation semis play for seventh.
update matchups m set game_type = 'seventh_place'
where m.week = 17 and m.game_type = 'consolation'
  and exists (select 1 from team_game_results r
              where r.team_id = m.team_a_id and r.week = 16
                and r.game_type = 'consolation' and r.result = 'W')
  and exists (select 1 from team_game_results r
              where r.team_id = m.team_b_id and r.week = 16
                and r.game_type = 'consolation' and r.result = 'W');

-- Week 17: their losers play for ninth.
update matchups m set game_type = 'ninth_place'
where m.week = 17 and m.game_type = 'consolation'
  and exists (select 1 from team_game_results r
              where r.team_id = m.team_a_id and r.week = 16
                and r.game_type = 'consolation' and r.result = 'L')
  and exists (select 1 from team_game_results r
              where r.team_id = m.team_b_id and r.week = 16
                and r.game_type = 'consolation' and r.result = 'L');


drop view if exists owner_all_time_stats;
drop view if exists season_results;
drop view if exists team_season_stats;


-- Final position 1-12, derived entirely from the placement games.
create view final_standings as
select
    t.season_year,
    t.team_id,
    t.owner_id,
    o.username,
    t.team_name,
    p.final_rank
from teams t
join owners o on o.owner_id = t.owner_id
left join (
    select
        r.team_id,
        case r.game_type
            when 'championship'   then case when r.result = 'W' then 1  else 2  end
            when 'third_place'    then case when r.result = 'W' then 3  else 4  end
            when 'fifth_place'    then case when r.result = 'W' then 5  else 6  end
            when 'seventh_place'  then case when r.result = 'W' then 7  else 8  end
            when 'ninth_place'    then case when r.result = 'W' then 9  else 10 end
            when 'eleventh_place' then case when r.result = 'W' then 11 else 12 end
        end as final_rank
    from team_game_results r
    where r.game_type in ('championship', 'third_place', 'fifth_place',
                          'seventh_place', 'ninth_place', 'eleventh_place')
) p on p.team_id = t.team_id;


create view team_season_stats as
select
    t.team_id,
    t.season_year,
    t.owner_id,
    o.username,
    t.team_name,
    fs.final_rank,
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
left join final_standings fs on fs.team_id = t.team_id
left join team_game_results r on r.team_id = t.team_id
group by t.team_id, t.season_year, t.owner_id, o.username, t.team_name, fs.final_rank;


create view season_results as
with champ as (
    select m.season_year,
           case when m.team_a_points > m.team_b_points then m.team_a_id else m.team_b_id end as champion_team_id,
           case when m.team_a_points > m.team_b_points then m.team_b_id else m.team_a_id end as runner_up_team_id
    from matchups m
    where m.game_type = 'championship'
      and m.team_a_points is not null and m.team_b_points is not null
),
third as (
    select m.season_year,
           case when m.team_a_points > m.team_b_points then m.team_a_id else m.team_b_id end as third_team_id
    from matchups m
    where m.game_type = 'third_place'
      and m.team_a_points is not null and m.team_b_points is not null
),
leader as (
    select distinct on (season_year) season_year, team_id, wins, losses
    from team_season_stats
    where games_played > 0
    order by season_year, wins desc, points_for desc
)
select
    s.season_year, s.is_complete,
    ct.owner_id as champion_owner_id, co.username as champion, ct.team_name as champion_team,
    rt.owner_id as runner_up_owner_id, ro.username as runner_up, rt.team_name as runner_up_team,
    ht.owner_id as third_owner_id, ho.username as third_place, ht.team_name as third_place_team,
    lt.owner_id as leader_owner_id, lo.username as regular_season_leader,
    lt.team_name as regular_season_leader_team,
    l.wins as leader_wins, l.losses as leader_losses
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
    round((sum(s.wins) + sum(s.ties) * 0.5)
          / nullif(sum(s.wins) + sum(s.losses) + sum(s.ties), 0), 3) as win_pct,
    count(*) filter (where s.made_playoffs)         as playoff_appearances,
    min(s.final_rank)                               as best_finish,
    round(avg(s.final_rank), 1)                     as avg_finish,
    (select count(*) from season_results sr where sr.champion_owner_id = s.owner_id) as titles,
    (select count(*) from season_results sr where sr.runner_up_owner_id = s.owner_id) as runner_ups
from team_season_stats s
group by s.owner_id, s.username;

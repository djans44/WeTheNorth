drop view if exists player_adp_rounds;

create view player_adp_rounds as
select
    a.season_year,
    a.player_id,
    p.full_name,
    p.position,
    a.adp,
    a.source,
    ceil(a.adp / s.team_count)::smallint as adp_round,
    least(ceil(a.adp / s.team_count)::smallint + 2, 13)::smallint as contract_cost_round
from player_adp a
join seasons s on s.season_year = a.season_year
join players p on p.player_id = a.player_id;

comment on view player_adp_rounds is
    'contract_cost_round is ADP round + 2, capped at 13 since the draft is 13 rounds.
     A player with no ADP row is treated as ADP round 13, giving a capped cost of 13.';

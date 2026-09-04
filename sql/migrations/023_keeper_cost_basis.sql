create view keeper_cost_basis as
select
    r.season_year,
    r.team_id,
    r.player_id,
    case
        when w.player_id is not null then 13
        else d.round
    end as basis_round,
    case
        when w.player_id is not null then 'waiver'
        when d.round is not null     then 'draft'
    end as basis_source,
    d.round      as drafted_round,
    d.team_id    as drafted_by_team_id
from rosters r
left join draft_picks d
       on d.season_year = r.season_year
      and d.player_id   = r.player_id
left join lateral (
    select t.player_id
    from transactions t
    where t.season_year = r.season_year
      and t.player_id   = r.player_id
      and t.kind        = 'add'
    limit 1
) w on true;

comment on view keeper_cost_basis is
    'What a player would cost to keep next season, before contracts.
     Any waiver or free agent pickup during the season sets the basis to round 13,
     whatever they were drafted at. A trade carries the basis with the player, so a
     drafted-then-traded player keeps their draft round.';

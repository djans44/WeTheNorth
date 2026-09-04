create view keeper_eligibility as
with prior as (
    select
        r.season_year + 1                as for_season,
        r.season_year                    as from_season,
        r.team_id,
        t.owner_id,
        r.player_id,
        b.basis_round,
        b.basis_source
    from rosters r
    join teams t on t.team_id = r.team_id
    join keeper_cost_basis b
      on b.season_year = r.season_year
     and b.player_id   = r.player_id
     and b.team_id     = r.team_id
),
history as (
    select player_id, max(keeper_year) as years_kept, max(season_year) as last_kept
    from keeper_selections
    group by player_id
),
contract as (
    select distinct on (player_id)
        player_id, contract_id, original_round, contract_round,
        signed_season, contract_years, status,
        signed_season + contract_years - 1 as final_season
    from keeper_contracts
    where status = 'active'
    order by player_id, signed_season desc
)
select
    p.for_season,
    p.team_id,
    p.owner_id,
    p.player_id,
    pl.full_name,
    pl.position,
    p.basis_round,
    p.basis_source,
    c.contract_id,
    c.signed_season,
    c.contract_years,
    c.final_season,
    coalesce(h.years_kept, 0) as years_kept,

    case
        when c.contract_id is not null and c.final_season >= p.for_season
            then 'contract'
        when c.contract_id is not null and c.final_season < p.for_season
            then 'ineligible_expired'
        when coalesce(h.years_kept, 0) >= 4
            then 'ineligible_max'
        when coalesce(h.years_kept, 0) = 1
            then 'must_sign'
        else 'free'
    end as state,

    case
        when c.contract_id is not null and c.final_season >= p.for_season then
            case when p.for_season = c.signed_season
                 then c.original_round else c.contract_round end
        else p.basis_round
    end as cost_round,

    case when c.contract_id is not null and c.final_season >= p.for_season
         then least(
             (case when p.for_season = c.signed_season
                   then c.original_round else c.contract_round end) + 3, 13)
    end as void_penalty_round,

    least(p.basis_round,
          coalesce(a.contract_cost_round, 13)) as contract_price_later
from prior p
join players pl on pl.player_id = p.player_id
left join history  h on h.player_id = p.player_id
left join contract c on c.player_id = p.player_id
left join player_adp_rounds a
       on a.player_id   = p.player_id
      and a.season_year = p.for_season;

comment on view keeper_eligibility is
    'One row per player on the previous season roster, with their state for the
     upcoming season. contract = must be kept, fills a slot, can only be escaped by
     voiding at void_penalty_round. must_sign = entering keeper year two, choose a
     1-year deal at cost_round or a 3-year deal at cost_round then
     contract_price_later twice. free = keepable at cost_round with no commitment.';

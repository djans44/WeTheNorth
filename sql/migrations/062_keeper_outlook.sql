-- What a player would cost to keep, as a function rather than a view.
--
-- keeper_eligibility answers this for every season but the one being played.
-- It reads `rosters`, which is the end-of-season snapshot, loaded in January
-- -- so there is no row for the season anyone is actually in, and the page
-- that would most like to show a keeper price is the page the view cannot
-- see. /rosters derives the current squad from the draft plus every move
-- since, and the view has never heard of that squad.
--
-- The obvious fix is to work the rule out again in the route. This project
-- has paid for that before: the round-collision rule once lived in six copies
-- and they did not agree. So the rule moves here, where the view calls it and
-- the route calls it, and the roster a caller has in hand -- stored or
-- derived -- is simply an argument.
--
-- The basis is the caller's to supply, because that is the part that genuinely
-- differs. keeper_cost_basis reads `rosters` for a finished season; a season
-- in progress works it out from the draft and the transactions. Both arrive
-- here as one number: the round this player would cost before contracts.
create function keeper_outlook(
    p_player_id  integer,
    p_for_season integer,
    p_basis_round integer
)
returns table (
    years_kept           integer,
    contract_id          integer,
    signed_season        smallint,
    contract_years       smallint,
    final_season         integer,
    state                text,
    cost_round           integer,
    void_penalty_round   integer,
    contract_price_later integer
)
language sql
stable
as $$
with kept as (
    -- The unbroken run of keeper years ending the season before this one,
    -- counted back a year at a time and stopped at the first gap. A year off
    -- -- forced by an expiring contract, or chosen by letting the player go
    -- -- starts the count over. Four steps is the whole range the rules
    -- allow: one plain year plus a three-year deal.
    select count(*)::int as years
    from generate_series(1, 4) as back(n)
    where exists (
        select 1 from keeper_selections s
        where s.player_id = p_player_id
          and s.season_year = p_for_season - back.n
    )
      and not exists (
        select 1 from generate_series(1, back.n - 1) as nearer(m)
        where not exists (
            select 1 from keeper_selections s2
            where s2.player_id = p_player_id
              and s2.season_year = p_for_season - nearer.m
        )
    )
),
con as (
    -- Only a contract that still bears on this season: one covering it, or
    -- one that ended last year and is costing its year off. An older contract
    -- belongs to a run that has already reset.
    select k.contract_id, k.original_round, k.contract_round,
           k.signed_season, k.contract_years,
           (k.signed_season + k.contract_years - 1)::int as final_season
    from keeper_contracts k
    where k.player_id = p_player_id
      and k.status = 'active'
      and k.signed_season + k.contract_years - 1 >= p_for_season - 1
    order by k.signed_season desc
    limit 1
)
select
    kept.years,
    c.contract_id,
    c.signed_season,
    c.contract_years,
    c.final_season,

    case
        when c.contract_id is not null and c.final_season >= p_for_season
            then 'contract'
        when c.contract_id is not null and c.final_season = p_for_season - 1
            then 'ineligible_expired'
        when kept.years >= 4 then 'ineligible_max'
        when kept.years = 1  then 'must_sign'
        else 'free'
    end,

    case
        when c.contract_id is not null and c.final_season >= p_for_season then
            (case when p_for_season = c.signed_season
                  then c.original_round else c.contract_round end)::int
        else p_basis_round
    end,

    case when c.contract_id is not null and c.final_season >= p_for_season
         then least(
             (case when p_for_season = c.signed_season
                   then c.original_round else c.contract_round end) + 3, 13)::int
    end,

    least(p_basis_round, coalesce(a.contract_cost_round, 13))::int
from kept
left join con c on true
left join player_adp_rounds a
       on a.player_id   = p_player_id
      and a.season_year = p_for_season;
$$;

comment on function keeper_outlook(integer, integer, integer) is
    'What one player would cost to keep in one season, given the round he
     would cost before contracts. The league''s keeper rule in one place:
     keeper_eligibility calls it for a finished season, and the rosters page
     calls it for the season being played, where no end-of-season roster
     exists yet.';


-- The view keeps its shape exactly -- same columns, same order, same types --
-- and stops carrying its own copy of the rule. Replaced rather than dropped,
-- because keeper_phase_plan is built on it.
create or replace view keeper_eligibility as
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
    o.contract_id,
    o.signed_season,
    o.contract_years,
    o.final_season,
    o.years_kept,
    o.state,
    o.cost_round,
    o.void_penalty_round,
    o.contract_price_later
from prior p
join players pl on pl.player_id = p.player_id
cross join lateral keeper_outlook(p.player_id, p.for_season, p.basis_round) o;

comment on view keeper_eligibility is
    'One row per player on the previous season roster, with their state for the
     upcoming season. contract = must be kept, fills a slot, can only be escaped by
     voiding at void_penalty_round. must_sign = entering keeper year two, choose a
     1-year deal at cost_round or a 3-year deal at cost_round then
     contract_price_later twice. free = keepable at cost_round with no commitment.
     The rule itself is keeper_outlook(), which the rosters page also calls for a
     season still being played.';

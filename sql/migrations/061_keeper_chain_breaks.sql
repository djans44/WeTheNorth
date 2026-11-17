-- The keeper chain breaks, and keeper_eligibility never knew it.
--
-- Two faults, one root: the view read a player's keeper history as a single
-- cumulative fact rather than as a run that can end.
--
-- FIRST, it never noticed a player going back into the draft. Garrett Wilson
-- was kept once, by Joey, in 2023. He has since been drafted by Borys, by
-- Chris and by David, three different owners in three straight years -- and
-- the view still called him must_sign, because max(keeper_year) over every
-- selection he has ever had is 1 and nothing looked at when. During the 2026
-- selection fifteen players were shown as owing a contract when they were
-- free to keep outright. That state is enforced, not decorative: the resolve
-- route refuses a submission with "Choose a contract term" when it sees it.
--
-- SECOND, the history CTE had no season filter at all, so it read selections
-- from for_season itself and every season after. Asking today what a player's
-- state was for 2023 was answered partly with 2024 and 2025 -- 161 rows carry
-- an answer contaminated that way. It is why the view looked worse under
-- inspection than it ever behaved live.
--
-- No contract was signed in error. All six 2026 contracts -- Nabers, Nix,
-- Maye, Irving, Smith-Njigba, Bowers -- were genuinely kept in 2025 and
-- genuinely owed one. The damage was fifteen players looking encumbered when
-- they were not.
--
-- The rule, as /rules states it and as the league confirms: a player is
-- ineligible for exactly one year after a contract expires, then resets to an
-- ordinary non-keeper, and the count starts over.
--
-- So years_kept is THE UNBROKEN RUN OF KEEPER YEARS ENDING THE SEASON BEFORE
-- THIS ONE, and that one definition settles every case. A contract expiring
-- forces a year off, which breaks the run. Simply not keeping a player breaks
-- it too. The four-season maximum then falls out of the contract mechanics --
-- one plain year plus at most a three-year deal -- rather than needing a rule
-- of its own, though it is kept below as a backstop.
--
-- Replaced rather than dropped: keeper_phase_plan is built on this view.
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
    c.contract_id,
    c.signed_season,
    c.contract_years,
    c.final_season,
    kept.years as years_kept,

    case
        when c.contract_id is not null and c.final_season >= p.for_season
            then 'contract'
        -- Exactly the year after, not every year after. This read
        -- final_season < for_season, which made one expiry a life sentence.
        when c.contract_id is not null and c.final_season = p.for_season - 1
            then 'ineligible_expired'
        when kept.years >= 4
            then 'ineligible_max'
        when kept.years = 1
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

-- How many keeper years this player has run up to, counting back from the
-- season before this one and stopping at the first gap.
--
-- Walked back a year at a time rather than found as an island of consecutive
-- seasons, because an island runs past for_season when the view is asked
-- about a year that has since been played -- which is the second fault above.
-- Four steps is the whole range: one plain year plus a three-year deal is the
-- most the rules allow.
left join lateral (
    select count(*)::int as years
    from generate_series(1, 4) as back(n)
    where exists (
        select 1 from keeper_selections s
        where s.player_id = p.player_id
          and s.season_year = p.for_season - back.n
    )
      and not exists (
        select 1 from generate_series(1, back.n - 1) as nearer(m)
        where not exists (
            select 1 from keeper_selections s2
            where s2.player_id = p.player_id
              and s2.season_year = p.for_season - nearer.m
        )
    )
) kept on true

-- Only a contract that still bears on this season: one covering it, or one
-- that ended last year and is costing its year off. An older contract belongs
-- to a keeper run that has already reset, and letting it through is what made
-- an expiry permanent.
left join lateral (
    select k.contract_id, k.original_round, k.contract_round,
           k.signed_season, k.contract_years,
           k.signed_season + k.contract_years - 1 as final_season
    from keeper_contracts k
    where k.player_id = p.player_id
      and k.status = 'active'
      and k.signed_season + k.contract_years - 1 >= p.for_season - 1
    order by k.signed_season desc
    limit 1
) c on true

left join player_adp_rounds a
       on a.player_id   = p.player_id
      and a.season_year = p.for_season;

comment on view keeper_eligibility is
    'One row per player on the previous season roster, with their state for the
     upcoming season. contract = must be kept, fills a slot, can only be escaped by
     voiding at void_penalty_round. must_sign = entering keeper year two, choose a
     1-year deal at cost_round or a 3-year deal at cost_round then
     contract_price_later twice. free = keepable at cost_round with no commitment.
     years_kept is the unbroken run ending the season before: a year off, whether
     forced by an expiring contract or chosen by letting the player go, starts it
     over.';

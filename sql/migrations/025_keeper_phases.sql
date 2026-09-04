create table keeper_windows (
    season_year smallint    not null references seasons (season_year),
    phase       smallint    not null check (phase between 1 and 3),
    opens_at    timestamptz not null,
    closes_at   timestamptz not null,
    resolved_at timestamptz null,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),

    primary key (season_year, phase),
    constraint keeper_windows_order check (closes_at > opens_at)
);

comment on column keeper_windows.resolved_at is
    'Set when the phase has been closed out and auto-submissions applied.
     Null means the phase has not been resolved yet, whatever the clock says.';


create table keeper_submissions (
    submission_id integer     generated always as identity primary key,
    season_year   smallint    not null,
    phase         smallint    not null check (phase between 1 and 3),
    owner_id      integer     not null references owners (owner_id),
    player_id     integer     null references players (player_id),
    cost_round    smallint    null check (cost_round between 1 and 13),
    contract_id   integer     null references keeper_contracts (contract_id),
    term_years    smallint    null check (term_years in (1, 3)),
    origin        text        not null default 'manual'
                  check (origin in ('manual', 'auto', 'forfeit')),
    status        text        not null default 'pending'
                  check (status in ('pending', 'approved', 'rejected')),
    submitted_at  timestamptz not null default now(),
    reviewed_by   integer     null references owners (owner_id),
    reviewed_at   timestamptz null,
    note          text        null,

    unique (season_year, phase, owner_id),
    constraint keeper_submissions_window_fk
        foreign key (season_year, phase) references keeper_windows (season_year, phase),
    constraint keeper_submissions_forfeit_is_empty
        check (origin <> 'forfeit' or player_id is null),
    constraint keeper_submissions_has_player
        check (origin = 'forfeit' or player_id is not null),
    constraint keeper_submissions_term_only_when_signing
        check (term_years is null or contract_id is null)
);

create index keeper_submissions_owner_idx on keeper_submissions (season_year, owner_id);


create table keeper_voids (
    season_year  smallint    not null,
    contract_id  integer     not null references keeper_contracts (contract_id),
    owner_id     integer     not null references owners (owner_id),
    penalty_round smallint   not null check (penalty_round between 1 and 13),
    declared_at  timestamptz not null default now(),

    primary key (season_year, contract_id)
);

comment on table keeper_voids is
    'Declared in phase 1 only, alongside that phase''s selection. Frees the slot
     immediately and obliges a defence pick at penalty_round.';


-- Which contract fills which phase, for one owner in one season.
create view keeper_phase_plan as
select
    c.season_year,
    c.owner_id,
    c.player_id,
    c.contract_id,
    c.cost_round,
    c.final_season - c.season_year + 1 as years_remaining,
    row_number() over (
        partition by c.season_year, c.owner_id
        order by c.final_season - c.season_year + 1 desc,
                 c.cost_round asc,
                 c.full_name asc
    )::smallint as phase
from (
    select
        k.for_season as season_year,
        k.owner_id,
        k.player_id,
        k.contract_id,
        k.cost_round,
        k.final_season,
        k.full_name
    from keeper_eligibility k
    where k.state = 'contract'
      and not exists (
          select 1 from keeper_voids v
          where v.contract_id = k.contract_id
            and v.season_year = k.for_season
      )
) c;

comment on view keeper_phase_plan is
    'Contracts occupy the earliest phases, longest remaining first, then earliest
     cost round, then player name. Voided contracts drop out and the phases behind
     them shift up.';

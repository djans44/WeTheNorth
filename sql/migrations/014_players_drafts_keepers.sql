create table players (
    player_id  integer     generated always as identity primary key,
    full_name  text        not null,
    position   text        not null check (position in ('QB','RB','WR','TE','K','DEF')),
    created_at timestamptz not null default now()
);

create unique index players_name_pos_key on players (lower(full_name), position);


create table draft_picks (
    draft_pick_id integer  generated always as identity primary key,
    season_year   smallint not null,
    round         smallint not null check (round between 1 and 13),
    pick_in_round smallint not null check (pick_in_round between 1 and 32),
    team_id       integer  not null,
    player_id     integer  null references players (player_id),
    is_keeper     boolean  not null default false,
    created_at    timestamptz not null default now(),

    constraint draft_picks_team_fk foreign key (team_id, season_year)
        references teams (team_id, season_year)
);

create unique index draft_picks_slot_key   on draft_picks (season_year, round, pick_in_round);
create unique index draft_picks_player_key on draft_picks (season_year, player_id);
create index        draft_picks_team_idx   on draft_picks (team_id);


create table rosters (
    season_year smallint not null,
    team_id     integer  not null,
    player_id   integer  not null references players (player_id),
    acquired    text     not null default 'draft'
                check (acquired in ('draft','waiver','trade')),
    created_at  timestamptz not null default now(),

    primary key (season_year, player_id),
    constraint rosters_team_fk foreign key (team_id, season_year)
        references teams (team_id, season_year)
);

create index rosters_team_idx on rosters (team_id);


-- One row per contract. Contracts follow the owner, not the season team.
create table keeper_contracts (
    contract_id    integer  generated always as identity primary key,
    player_id      integer  not null references players (player_id),
    owner_id       integer  not null references owners (owner_id),
    original_round smallint not null check (original_round between 1 and 13),
    signed_season  smallint not null references seasons (season_year),
    contract_years smallint not null check (contract_years in (1, 3)),
    adp_round      smallint null check (adp_round between 1 and 20),
    status         text     not null default 'active'
                   check (status in ('active','voided','expired')),
    voided_season  smallint null references seasons (season_year),
    penalty_round  smallint null,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),

    constraint keeper_contracts_adp_required
        check (contract_years = 1 or adp_round is not null),
    constraint keeper_contracts_void_fields
        check ((status = 'voided') = (voided_season is not null))
);

create unique index keeper_contracts_player_signed_key
    on keeper_contracts (player_id, signed_season);


-- One row per player kept in a season, recording what was actually paid.
create table keeper_selections (
    season_year smallint not null,
    team_id     integer  not null,
    player_id   integer  not null references players (player_id),
    cost_round  smallint not null check (cost_round between 1 and 13),
    keeper_year smallint not null check (keeper_year between 1 and 4),
    contract_id integer  null references keeper_contracts (contract_id),
    created_at  timestamptz not null default now(),

    primary key (season_year, player_id),
    constraint keeper_selections_team_fk foreign key (team_id, season_year)
        references teams (team_id, season_year),
    constraint keeper_selections_year_one_has_no_contract
        check (keeper_year > 1 or contract_id is null)
);

create unique index keeper_selections_cost_key
    on keeper_selections (season_year, team_id, cost_round);

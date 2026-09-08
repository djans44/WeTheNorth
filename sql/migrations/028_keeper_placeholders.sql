create table keeper_placeholders (
    season_year smallint    not null references seasons (season_year),
    owner_id    integer     not null references owners (owner_id),
    player_id   integer     not null references players (player_id),
    cost_round  smallint    not null check (cost_round between 1 and 13),
    created_at  timestamptz not null default now(),
    created_by  integer     null references owners (owner_id),

    primary key (season_year, owner_id, player_id),
    unique (season_year, owner_id, cost_round)
);

comment on table keeper_placeholders is
    'Hypothetical keepers for draft planning. Never affects keeper_submissions or
     eligibility. A real submission for the same owner and round takes precedence
     on the board.';

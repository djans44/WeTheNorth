create table rivalries (
    season_year    smallint    not null references seasons (season_year),
    owner_id       integer     not null references owners (owner_id),
    rival_owner_id integer     not null references owners (owner_id),
    score          numeric(8,2) null,
    source         text        not null default 'auto'
                   check (source in ('auto', 'manual')),
    created_at     timestamptz not null default now(),
    created_by     integer     null references owners (owner_id),

    primary key (season_year, owner_id),
    constraint rivalries_not_self check (owner_id <> rival_owner_id)
);

create unique index rivalries_rival_key on rivalries (season_year, rival_owner_id);

comment on table rivalries is
    'One row per owner per season, written in both directions so the pairing is
     symmetric. The unique index on rival_owner_id stops anyone being two
     managers'' rival.';

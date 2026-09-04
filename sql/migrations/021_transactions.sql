create table transactions (
    transaction_id integer     generated always as identity primary key,
    season_year    smallint    not null references seasons (season_year),
    kind           text        not null check (kind in ('add', 'trade')),
    method         text        null
                   check (method in ('free_agent', 'waiver', 'faab', 'commissioner')),
    faab_amount    numeric(6,2) null,
    player_id      integer     not null references players (player_id),
    to_team_id     integer     not null,
    from_team_id   integer     null,
    occurred_on    date        null,
    occurred_raw   text        null,
    created_at     timestamptz not null default now(),

    constraint transactions_to_team_fk foreign key (to_team_id, season_year)
        references teams (team_id, season_year),
    constraint transactions_from_team_fk foreign key (from_team_id, season_year)
        references teams (team_id, season_year),
    constraint transactions_trade_has_source
        check (kind <> 'trade' or from_team_id is not null),
    constraint transactions_add_has_method
        check (kind <> 'add' or method is not null)
);

create index transactions_player_idx on transactions (season_year, player_id);
create index transactions_team_idx   on transactions (to_team_id);

comment on column transactions.season_year is
    'The league season. Source files carry no year, so this comes from the import
     argument and the calendar year is derived from the month.';
comment on column transactions.occurred_on is
    'Calendar date. Months from August onward belong to season_year, earlier
     months to season_year + 1.';

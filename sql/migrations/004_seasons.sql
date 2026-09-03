create table seasons (
    season_year      smallint    primary key check (season_year between 2005 and 2100),
    team_count       smallint    not null check (team_count between 2 and 32),
    keeper_count     smallint    null check (keeper_count >= 0),
    is_complete      boolean     not null default false,
    yahoo_league_key text        null,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

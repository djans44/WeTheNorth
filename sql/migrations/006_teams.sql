create table teams (
    team_id     integer     generated always as identity primary key,
    season_year smallint    not null references seasons (season_year),
    owner_id    integer     not null references owners (owner_id),
    team_name   text        not null,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create unique index teams_season_owner_key on teams (season_year, owner_id);
create unique index teams_id_season_key    on teams (team_id, season_year);

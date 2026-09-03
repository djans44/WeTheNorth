create table matchups (
    matchup_id     integer     generated always as identity primary key,
    season_year    smallint    not null references seasons (season_year),
    week           smallint    not null check (week between 1 and 20),
    game_type      text        not null default 'regular'
                   check (game_type in ('regular', 'quarterfinal', 'semifinal',
                                        'championship', 'third_place', 'consolation')),
    team_a_id      integer     not null,
    team_b_id      integer     not null,
    team_a_points  numeric(6,2) null check (team_a_points >= 0),
    team_b_points  numeric(6,2) null check (team_b_points >= 0),
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),

    constraint matchups_team_order check (team_a_id < team_b_id),

    constraint matchups_team_a_fk foreign key (team_a_id, season_year)
        references teams (team_id, season_year),
    constraint matchups_team_b_fk foreign key (team_b_id, season_year)
        references teams (team_id, season_year)
);

create unique index matchups_unique_pairing
    on matchups (season_year, week, team_a_id, team_b_id);

create index matchups_team_a_idx on matchups (team_a_id);
create index matchups_team_b_idx on matchups (team_b_id);

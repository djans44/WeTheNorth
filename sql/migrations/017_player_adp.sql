create table player_adp (
    season_year smallint not null references seasons (season_year),
    player_id   integer  not null references players (player_id),
    adp         numeric(5,1) not null check (adp > 0),
    source      text     null,
    captured_on date     null,
    created_at  timestamptz not null default now(),

    primary key (season_year, player_id)
);

create view player_adp_rounds as
select
    a.season_year,
    a.player_id,
    p.full_name,
    p.position,
    a.adp,
    a.source,
    ceil(a.adp / s.team_count)::smallint as adp_round,
    ceil(a.adp / s.team_count)::smallint + 2 as contract_cost_round
from player_adp a
join seasons s on s.season_year = a.season_year
join players p on p.player_id = a.player_id;

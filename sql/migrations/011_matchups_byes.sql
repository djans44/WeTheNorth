alter table matchups alter column team_b_id drop not null;

alter table matchups
    add constraint matchups_bye_has_no_opponent_scores
    check (
        team_b_id is not null
        or (team_b_points is null and team_b_projected is null)
    );

drop index matchups_unique_pairing;

create unique index matchups_unique_pairing
    on matchups (season_year, week, team_a_id, team_b_id)
    nulls not distinct;

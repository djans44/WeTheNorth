alter table matchups
    add column team_a_projected numeric(6,2) null check (team_a_projected >= 0),
    add column team_b_projected numeric(6,2) null check (team_b_projected >= 0);

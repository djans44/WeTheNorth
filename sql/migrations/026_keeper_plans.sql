create table keeper_plans (
    season_year smallint    not null,
    phase       smallint    not null check (phase between 1 and 3),
    owner_id    integer     not null references owners (owner_id),
    player_id   integer     null references players (player_id),
    term_years  smallint    null check (term_years in (1, 3)),
    updated_at  timestamptz not null default now(),
    updated_by  integer     null references owners (owner_id),

    primary key (season_year, phase, owner_id),
    constraint keeper_plans_window_fk
        foreign key (season_year, phase) references keeper_windows (season_year, phase)
);

comment on table keeper_plans is
    'An owner''s intended pick for a phase, editable until that phase resolves.
     At resolution a plan becomes a submission with origin = plan. A phase with no
     plan and no contract forfeits.';

alter table keeper_submissions drop constraint keeper_submissions_origin_check;
alter table keeper_submissions add constraint keeper_submissions_origin_check
    check (origin in ('manual', 'plan', 'auto', 'forfeit'));

alter table keeper_voids
    add column confirmed_at timestamptz null;

comment on column keeper_voids.confirmed_at is
    'A void is declared during phase 1 planning and written immediately, because it
     changes which contracts fill which phases. It becomes binding when phase 1
     resolves, which is when confirmed_at is set. Until then it can be withdrawn.';

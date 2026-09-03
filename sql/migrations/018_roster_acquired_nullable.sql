alter table rosters alter column acquired drop not null;
alter table rosters alter column acquired drop default;

comment on column rosters.acquired is
    'How the player joined this roster. Null means not yet determined from transactions.';

create table draft_order (
    season_year      smallint    not null references seasons (season_year),
    owner_id         integer     not null references owners (owner_id),
    lottery_position smallint    not null check (lottery_position between 1 and 32),
    slot             smallint    null check (slot between 1 and 32),
    chosen_at        timestamptz null,
    chosen_by        integer     null references owners (owner_id),
    created_at       timestamptz not null default now(),

    primary key (season_year, owner_id)
);

create unique index draft_order_position_key on draft_order (season_year, lottery_position);
create unique index draft_order_slot_key     on draft_order (season_year, slot);

comment on table draft_order is
    'lottery_position is the order in which owners choose. slot is the draft
     position they chose, null until they pick. Slot choice happens before
     keeper selection.';


create view draft_order_state as
select
    d.season_year,
    d.owner_id,
    o.username,
    d.lottery_position,
    d.slot,
    d.chosen_at,
    (d.slot is null
     and d.lottery_position = (
         select min(x.lottery_position) from draft_order x
         where x.season_year = d.season_year and x.slot is null
     )) as is_on_the_clock
from draft_order d
join owners o on o.owner_id = d.owner_id;

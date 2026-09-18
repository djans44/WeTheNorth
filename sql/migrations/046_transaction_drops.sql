-- Drops, and a natural key so pasting the same page twice is harmless.
--
-- Transactions were loaded once a year from a file, so the importer could
-- replace the whole season and never think about what was already there.
-- Loading week by week from the site is a different job: the page you copy
-- from is cumulative, so every paste overlaps the last one, and the thing
-- that decides what is new has to be the data itself rather than a date the
-- admin remembers to type.
--
-- occurred_raw is what Yahoo printed -- "Sep 16, 4:52 am" -- and it is the
-- part that makes the key sharp. Two adds of the same player by the same
-- team in the same minute do not happen. Checked against the 301 rows of
-- 2025 before writing this: no collisions.
--
-- Drops arrive in the same blocks as the adds, because a waiver claim names
-- the player who made room. Nothing computes from them yet -- keeper cost
-- basis keys off adds, and the two transaction crests count adds and trades
-- -- but the file has been throwing them away and the history is the point.

begin;

-- A drop leaves a team and joins nobody, so the target cannot stay required.
alter table transactions alter column to_team_id drop not null;

alter table transactions drop constraint transactions_kind_check;
alter table transactions add constraint transactions_kind_check
    check (kind in ('add', 'trade', 'drop'));

-- Each kind says which way the player moved, and the constraints keep a row
-- from claiming both or neither.
alter table transactions add constraint transactions_drop_has_source
    check (kind <> 'drop' or (from_team_id is not null and to_team_id is null));
alter table transactions add constraint transactions_add_has_target
    check (kind <> 'add' or to_team_id is not null);
alter table transactions add constraint transactions_trade_has_target
    check (kind <> 'trade' or to_team_id is not null);

-- nulls not distinct, or a drop (to_team_id null) would never collide with
-- the same drop pasted again: every null is unique without it, and the index
-- would allow exactly what it is here to forbid.
create unique index transactions_natural
    on transactions (season_year, kind, player_id, to_team_id,
                     from_team_id, occurred_raw)
    nulls not distinct;

comment on index transactions_natural is
    'What makes a weekly paste idempotent: re-pasting a page already loaded
     collides here instead of doubling the season. occurred_raw is Yahoo''s
     own string, times included, so it is exact rather than rounded to a day.';

commit;

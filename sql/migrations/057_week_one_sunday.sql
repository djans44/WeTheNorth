-- When a season's first week is, and therefore when all of them are.
--
-- Nothing in the schema could say which week a date fell in. matchups has no
-- date, seasons had none either, and transactions.occurred_on is a bare date
-- -- so 61 trades across 2022-25 could not be attributed to a week, and the
-- transactions page could not group by one. One column fixes all of it,
-- because NFL weeks are exactly seven days apart and the rest is arithmetic.
--
-- The Sunday rather than the Tuesday the fantasy week opens, for three
-- reasons. It is checkable against any NFL schedule, where a Tuesday is a
-- number nobody recognises. It is unambiguous, where "the Tuesday" invites
-- whoever types it to pick the wrong side of the weekend and shift every week
-- of the year by five days. And the Sunday is what the league is about.
--
-- A week runs from the Tuesday before its Sunday to the Monday after it. That
-- boundary is not a guess: across 2551 loaded transactions, Wednesday carries
-- 1317 of them -- the waiver clear -- and Monday, the last day of a fantasy
-- week, carries 51. With these five Sundays every transaction falls in weeks
-- 1 to 17 bar 30 preseason moves and two dated the first of January.
alter table seasons add column week_one_sunday date;

-- It has to be a Sunday. The whole column is the claim that it is one, and a
-- Tuesday typed in here would quietly move every week of the season by five
-- days -- no error, no empty page, just every date wrong in the same
-- direction, which is the hardest kind of wrong to notice.
alter table seasons add constraint seasons_week_one_is_sunday
    check (week_one_sunday is null or extract(isodow from week_one_sunday) = 7);

-- The Sunday after the Thursday after Labor Day, which is where the NFL has
-- started every season the league has run. Each of these five was checked
-- against that rule and against the weekday before being written down.
update seasons set week_one_sunday = '2022-09-11' where season_year = 2022;
update seasons set week_one_sunday = '2023-09-10' where season_year = 2023;
update seasons set week_one_sunday = '2024-09-08' where season_year = 2024;
update seasons set week_one_sunday = '2025-09-07' where season_year = 2025;
update seasons set week_one_sunday = '2026-09-13' where season_year = 2026;

-- Which week a date belongs to. One definition, in the database, because two
-- would drift: this project has been bitten by the same rule living in six
-- copies before now.
--
-- Zero means before the season started, which is a real answer and not an
-- error -- every season's first move predates week one, drafts being held in
-- August. Null means the season has no anchor yet. Past 17 is equally real:
-- somebody tinkering with a roster on New Year's Day.
--
-- Integer division is the reason for the explicit branch. In SQL it truncates
-- toward zero rather than flooring, so a date in the week before week one
-- would come back as week 1 instead of 0.
create function league_week(p_season integer, p_day date)
returns integer
language sql
stable
as $$
    select case
             when s.week_one_sunday is null or p_day is null then null
             when p_day < s.week_one_sunday - 5 then 0
             else (p_day - (s.week_one_sunday - 5)) / 7 + 1
           end
      from seasons s
     where s.season_year = p_season
$$;

comment on function league_week(integer, date) is
    'The league week a date falls in: 0 before week one, null with no anchor.';

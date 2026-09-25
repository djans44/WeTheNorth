-- When the draft was held.
--
-- The third of the three dates nobody owned, and the last. Unlike the other
-- two it is not measured from anything and does not gate anything: it is a
-- day people turn up to, and the reason to record it is that a year later
-- nobody remembers when it was.
--
-- Not derived from week one, though it is tempting. 2026's draft fell on the
-- Tuesday that opens week one, which would make it week_one_sunday - 5 and no
-- column at all -- but that is where this league happened to put it once, not
-- a rule, and a derivation has no way to be corrected the year it is wrong.
alter table seasons add column draft_on date;

-- Before the season's first Sunday. A draft after the games have started is
-- not a draft.
alter table seasons add constraint seasons_draft_before_week_one
    check (draft_on is null or week_one_sunday is null
           or draft_on < week_one_sunday);

-- 2026, as told to the commissioner's chronicler: the draft on the Tuesday
-- before the season started, and trading closing on the twenty-eighth of
-- November -- a Saturday, in week 12, which sits comfortably later than any
-- trade the league has ever actually made. Of 63 trade sides across four
-- previous seasons the latest falls in week 11.
--
-- 2022-25 are left null. What those seasons ran under is not written down
-- anywhere, and a date inferred from where the trades happen to stop would be
-- a guess wearing the clothes of a record.
update seasons set draft_on = '2026-09-08', trade_deadline = '2026-11-28'
 where season_year = 2026;

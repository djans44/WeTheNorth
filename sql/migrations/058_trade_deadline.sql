-- When trading stops.
--
-- A real rule with consequences, not a note: the commissioner refuses trades
-- after it. Nothing in the app can prevent one -- trades happen in Yahoo and
-- arrive here through the transaction import -- but with the date stored it
-- can catch one that landed late, which is the same shape as the roster
-- reconciliation: the record checking itself.
--
-- A date rather than a week. A week would derive itself from week_one_sunday
-- every year with nothing to re-enter, and the record even suggests which one
-- -- of 63 trade sides across four seasons the latest falls in week 11, and
-- three of the four seasons stop at week 10 or 11. But the league sets this by
-- hand each year and a stored week would be the app telling the league what
-- its own rule is.
--
-- Left null for 2022-25. The deadline those seasons ran under is not written
-- down anywhere, and inferring one from where the trades happen to stop would
-- be inventing a rule and then presenting it as a record.
alter table seasons add column trade_deadline date;

-- After the season starts, which is the only part of "is this a sensible
-- deadline" that can be checked without knowing the league's mind. Both null
-- is allowed, because a season row is created before either is decided.
alter table seasons add constraint seasons_deadline_after_week_one
    check (trade_deadline is null or week_one_sunday is null
           or trade_deadline > week_one_sunday);

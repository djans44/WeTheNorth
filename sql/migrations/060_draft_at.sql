-- The draft has a time, not only a day.
--
-- It is the one date on the calendar that people turn up to at an hour, so a
-- bare date was never enough: "the draft is on the eighth" leaves twelve
-- people asking when. The keeper windows already work this way, and this is
-- the same shape as those -- entered as league wall-clock and stored as the
-- instant that was typed.
alter table seasons add column draft_at timestamptz;

-- Carry what is recorded across, read as league time. 2026 is the only season
-- that has one, and it comes over at midnight because a date is all there was
-- -- the hour has to be set by hand, and the form is where.
update seasons
   set draft_at = (draft_on::timestamp at time zone 'America/Toronto')
 where draft_on is not null;

-- The "before week one" rule goes back to being checked in Python only. The
-- expression that would hold it here -- the instant read as a league date --
-- is STABLE rather than IMMUTABLE, because it depends on timezone rules that
-- can change, and a CHECK cannot hold one of those. The form already refuses
-- a draft after the season starts and says why, which is the half that talks
-- to a person anyway.
alter table seasons drop constraint seasons_draft_before_week_one;
alter table seasons drop column draft_on;

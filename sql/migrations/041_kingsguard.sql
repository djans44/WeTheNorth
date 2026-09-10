-- Captain of the Kingsguard: the longest winning streak still running.
--
-- The eighth title, and the only one that can be lost without anyone taking
-- it from you -- lose a game and it goes to whoever is still unbeaten. The
-- rest change hands when someone does better; this one changes hands when
-- you do worse.
--
-- Regular season only, like every other streak rule on the site. A playoff
-- run is a different thing and the bracket already says who went furthest.
--
-- A streak does not cross a season, so "still running" means the trailing
-- run of wins in the newest season that has been played. Between seasons
-- that is whoever finished the last one hottest, which is the right answer:
-- they carry it until the first week of the new one settles it.
--
-- Ties go to the points scored during the streak, not to a career total.
-- Two managers on four straight is entirely likely and the crest is about
-- that run, so the run should decide it.
--
-- sort_order 160 puts it above Bane of Their Rival and below The
-- Ever-Victorious, which is what decides the ring when a manager holds more
-- than one: the throne first, then the best record over all of it, then the
-- best run going on right now.

insert into crests
    (code, name, description, category, scope, standing, award_mode,
     colour, sort_order)
values
('kingsguard', 'Captain of the Kingsguard',
 'The longest winning streak still running, in the regular season. A tie '
 'goes to the most points scored during the streak.',
 'scoring', 'career', 'held', 'auto', 'silver', 160);

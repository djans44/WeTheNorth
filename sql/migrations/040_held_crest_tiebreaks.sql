-- The last two shared held crests name their tiebreaks.
--
-- 039 settled The Ever-Victorious. These are the other two that closed a
-- season with more than one holder, and they need different answers.
--
-- Master of Storms counted three top weeks each for Joey and Tulio at the end
-- of 2022. A count that low ties easily, so it goes to points scored, the
-- same tiebreak as The Ever-Victorious.
--
-- Bane of Their Rival had Joey and Tom both 4-0 over four meetings at the end
-- of 2023 -- tied on rate, and then tied again on the meetings tiebreak that
-- already existed. It goes to points scored *in those meetings*, not to a
-- career total. This is a crest about one rivalry, and a career total is
-- mostly games against everybody else; a manager who scored heavily against
-- the other ten should not take a crest for it.

update crests
   set description = 'Has topped the league''s scoring in more weeks than '
                     'anyone. A tie goes to the most points scored.'
 where code = 'most_weekly_highs';

update crests
   set description = 'The best win rate against their own rival, over at '
                     'least four meetings. A tie goes to the most meetings, '
                     'then to the most points scored in them.'
 where code = 'fiercest_rival';

-- Legend of the Choosing: held the whole way, not merely there at both ends.
--
-- The description said "still on the roster when the season ended", and the
-- query tested exactly that -- drafted by X, on X's roster in the snapshot.
-- It let through a player who was dropped in September and re-signed in the
-- last week of December, because both ends said the same name and nothing
-- looked at the middle. Borys and Luther Burden III, ten transactions apart.
--
-- The crest is about the middle. Any transaction at all now disqualifies.

begin;

update crests
   set description = 'The best pick of the late rounds: taken in round ten or after, not kept, and held from the draft to the end of the season without once changing hands.'
 where code = 'draft_day';

commit;

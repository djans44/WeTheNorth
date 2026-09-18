-- Legend of the Choosing becomes the best late pick.
--
-- "For conduct at the draft" was an opinion about a person and the ballot
-- was twelve names with nothing to choose between them -- a popularity vote
-- wearing a crest. The draft already records something worth arguing about:
-- who found a player late that nobody else wanted.
--
-- Round ten or later, keepers excluded -- a keeper is not a pick, it is a
-- price paid -- and still on that manager's roster when the season ended,
-- which is what separates finding a player from taking a flier on one. Nine
-- picks in 2025 clear all three.

begin;

update crests
   set description = 'The best pick of the late rounds: taken in round ten or after, not kept, and still on the roster when the season ended.'
 where code = 'draft_day';

commit;

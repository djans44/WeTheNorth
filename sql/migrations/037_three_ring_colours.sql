-- Three ring colours, not seven.
--
-- Seven held crests had seven colours, which asked a 24px circle to carry
-- seven distinct meanings. It cannot. The ring now says only three things and
-- the mark overlaid on the sigil says which crest it is:
--
--   gold    the current champion, and nothing else
--   silver  a crest it is good to hold
--   bronze  a crest it is not
--
-- Which means a manager wearing bronze is wearing it for one of two reasons,
-- and the mark is what separates the wooden spoon from a habit of losing by
-- inches.

update crests set colour = 'gold'   where code = 'reigning_champion';

update crests set colour = 'silver'
 where code in ('best_record', 'fiercest_rival', 'most_weekly_highs',
                'most_narrow_wins');

update crests set colour = 'bronze'
 where code in ('most_narrow_losses', 'reigning_sacko');

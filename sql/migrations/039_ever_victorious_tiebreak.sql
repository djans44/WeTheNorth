-- The Ever-Victorious names its tiebreak.
--
-- Records repeat. 2023 closed with Josh, Joey and David all on 18-10, which
-- is one crest and three claimants, and a title only one manager holds cannot
-- be settled by the number they are tied on. It goes to whoever scored the
-- most, which is the league's own tiebreak everywhere else it needs one --
-- the wildcard seeds are decided on points for.
--
-- The description carries the rule because it is the hover text on the crest,
-- and the alternative was a reader seeing three identical records and one
-- crest with no way to tell why.

update crests
   set description = 'The best win rate in league history, over at least two '
                     'seasons. A tie goes to the most points scored.'
 where code = 'best_record';

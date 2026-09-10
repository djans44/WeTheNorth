-- The Court Fool, the streaks crossing seasons, and a renamed throne.
--
-- Streaks now run across a season boundary. Win your last three of one year
-- and your first two of the next and that is five, not two -- which is what
-- a streak means everywhere except in a table that happens to be filed by
-- season. The cost is that a manager who leaves the league keeps their last
-- run forever, with no loss ever coming to end it, so a retired owner is
-- exempt from both streak titles. Theo finished 2023 on two wins and would
-- otherwise have been Captain of the Kingsguard for parts of 2025.
--
-- Both streak titles can now sit vacant, which none of the others can. Two
-- straight wins to be Captain, three straight losses to be Fool: below that
-- nobody holds it, because a title that always belongs to somebody says
-- nothing when a run of one is the best on offer.
--
-- The Fool's tie goes to the FEWEST points scored during the run. It is the
-- one crest on the site where less is worse, which is the point of it.
--
-- And the throne is Protector of the Realm. Sitter of the Iron Throne was
-- the joke version; this is the one that reads on a shield.

update crests
   set name = 'Protector of the Realm'
 where code = 'reigning_champion';

update crests
   set description = 'The longest winning streak still running, at least two '
                     'games, across seasons. A tie goes to the most points '
                     'scored during the streak.'
 where code = 'kingsguard';

insert into crests
    (code, name, description, category, scope, standing, award_mode,
     colour, sort_order)
values
('court_fool', 'The Court Fool',
 'The longest losing streak still running, at least three games, across '
 'seasons. A tie goes to the fewest points scored during the streak.',
 'scoring', 'career', 'held', 'auto', 'bronze', 280);

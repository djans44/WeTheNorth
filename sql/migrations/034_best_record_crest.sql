-- A fourth held crest: the best win rate the league has ever kept.
--
-- Silver for the ring, the three other held crests having taken gold, bronze
-- and oxblood.
--
-- It sits between the throne and the rival in sort_order, so a manager holding
-- both the title and the record wears the throne's gold ring and their page
-- lists both.

insert into crests
    (code, name, description, category, scope, standing, award_mode, colour, sort_order)
values
('best_record', 'The Ever-Victorious',
 'The best win rate in league history, over at least two seasons.',
 'silverware', 'career', 'held', 'auto', 'silver', 150);

-- Two all-time crests for dealing, and a name freed up to make one of them.
--
-- Master of Coin was the season's busiest trader, and its charge has always
-- been two stacks of coins -- which is a picture of money, not of dealing.
-- The name and the art both go where they belong: to the manager who has
-- spent the most on waivers across every season. Nothing else in the league
-- is measured in money at all.
--
-- The season crest keeps its code and its meaning and takes a new name and a
-- new charge. Traffic in Men, because that is what a trade is: moving people
-- for advantage, which the league does without blinking and the phrasing
-- ought to notice.
--
-- And Master of Whisperers for the most trades across every season. A small
-- council office, so the two career crests stand as a matched pair, and a
-- trade is a thing arranged quietly before anyone announces it.

begin;

update crests set name = 'Traffic in Men',
                  description = 'Most trades of the season.'
 where code = 'wheeler_dealer';

insert into crests (code, name, description, category, scope, standing,
                    award_mode, colour, sort_order, active)
values
    ('most_faab', 'Master of Coin',
     'Most spent on waivers, across every season.',
     'transactions', 'career', 'earned', 'auto', 'gold',
     (select max(sort_order) + 1 from crests), true),
    ('most_trades', 'Master of Whisperers',
     'Most trades made, across every season.',
     'transactions', 'career', 'earned', 'auto', 'silver',
     (select max(sort_order) + 2 from crests), true);

commit;

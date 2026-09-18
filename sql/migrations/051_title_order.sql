-- The two new titles belong among the silver, not after the bronze.
--
-- They were given max(sort_order) + 1 when they were created, which put them
-- at 8301 and 8302 -- past every season crest in the catalogue, so the Titles
-- section listed them below Lord of the Wastes. The band they belong in is
-- the silver one: 150 to 260, with bronze from 270.
--
-- sort_order is not only the reading order. The ring a manager wears is the
-- lowest sort_order they hold, so this also says a silver title outranks a
-- bronze one for the sigil -- which it should, and did not while these sat
-- at the end. Curtis holds Hounded by Fate (270) and Master of Whisperers,
-- so his ring moves from bronze to silver. Joey holds Bane of Their Rival
-- (200), which still outranks Master of Coin, so his does not change.
--
-- And Master of Coin is silver. Gold is the championship alone.

begin;

update crests set sort_order = 262, colour = 'silver' where code = 'most_faab';
update crests set sort_order = 264 where code = 'most_trades';

commit;

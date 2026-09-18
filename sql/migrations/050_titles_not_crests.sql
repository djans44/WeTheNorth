-- Master of Coin and Master of Whisperers are titles, not crests.
--
-- They were asked for as titles and built as earned crests, which put them
-- in The market beside the season ones and left the Titles section without
-- them. The difference is not decoration: a title is held by one manager at
-- a time, it rings their sigil, and the page lists who it was taken from.
-- Both of these are exactly that -- most spent ever, most traded ever, one
-- holder, held until passed.
--
-- The ring goes to the lowest sort_order a manager holds and these are last,
-- so nobody who already wears one changes; only a manager holding no other
-- title gains one.

begin;

update crests set standing = 'held' where code in ('most_faab', 'most_trades');

commit;

-- The two career titles say what they cost to win.
--
-- A tie used to be shared, which is right for a crest and wrong for a title:
-- early in a league's life "most trades ever" is two people on one trade
-- each, and that is not a reign. Both now take a floor and then seniority --
-- level totals go to whoever reached them first, so a title is held until
-- somebody genuinely passes its holder rather than draws level with them.
--
-- The floors are in app/crests.py as COIN_FLOOR and WHISPER_FLOOR. They are
-- named in the description as well because a manager reading the catalogue
-- should be able to see what it takes without reading the code.

begin;

update crests
   set description = 'Most spent on waivers, across every season. Twenty-one dollars at least, and level totals go to whoever got there first.'
 where code = 'most_faab';

update crests
   set description = 'Most trades made, across every season. Three at least, and level counts go to whoever got there first.'
 where code = 'most_trades';

commit;

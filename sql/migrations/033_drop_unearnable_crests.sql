-- Three crests that cannot honestly be awarded come out of the catalogue.
--
-- four_year_man      Held one player four straight seasons. There have been
--                    three keeper seasons and the longest hold is three, so
--                    it has never been reachable.
--
-- set_and_forget     A season with no waiver adds. Only 2025 has transaction
--                    rows at all; over 2022-2024 the crest would have gone to
--                    all twelve managers because the table is empty, not
--                    because anyone sat still.
--
-- forfeit            Missed a keeper window. keeper_submissions holds no rows,
--                    so nothing can tell a forfeit from a manager who simply
--                    had fewer than three to keep. Left in the catalogue it
--                    would have read as "nobody has ever missed one", when in
--                    truth nothing was looking.

delete from owner_crests
 where crest_id in (select crest_id from crests
                     where code in ('four_year_man', 'set_and_forget', 'forfeit'));

delete from crests
 where code in ('four_year_man', 'set_and_forget', 'forfeit');

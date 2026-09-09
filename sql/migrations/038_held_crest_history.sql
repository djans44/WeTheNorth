-- owner_crests.season_year takes a third meaning, for held crests.
--
-- It was: the season an earned crest belongs to, and null for everything
-- career-shaped, held crests included, because "who holds it now" has no
-- season to sit in.
--
-- A held crest now writes a row per finished season as well. The season page
-- has to ring its sigils with the titles as they stood when that season
-- closed rather than the ones being worn today, the season's honour roll has
-- to name who ended the year holding what, and a manager who once held a
-- title and lost it should be able to say so. None of that can be recovered
-- from a single live row, and recomputing it per request means running the
-- seven rules over every season on every page view.
--
-- So, for a held crest:
--
--   season_year is null   who holds it today
--   season_year = 2023    who held it at the close of 2023
--
-- Both are auto rows and both are rebuilt by scripts/award_crests.py. The
-- existing partial unique indexes already carry this: owner_crests_career_uniq
-- keeps one live row per manager per crest, owner_crests_season_uniq one per
-- manager per crest per season. Ties are shared, as they always have been, so
-- a season can close with two managers holding the same title.
--
-- No schema change -- the column is already nullable and already references
-- seasons. This migration exists so the meaning is discoverable in the
-- database rather than only in the comment on migration 031, which now
-- describes only half of it.

comment on column owner_crests.season_year is
    'Earned crest: the season it was won, null if career-wide. Held crest:
     null is the current holder, a year is who held it when that season
     closed. Rebuilt by scripts/award_crests.py.';

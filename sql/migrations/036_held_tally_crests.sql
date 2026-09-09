-- Three more held crests, all of them tallies rather than single results.
--
-- These count how often something has happened across a manager's whole
-- career, so they change hands the week someone overtakes the holder. That
-- makes them the reason held crests are recomputed after every week rather
-- than only after a season.
--
-- Counted from game_log directly, not from owner_crests rows. Counting the
-- rows would make one crest depend on another already having been computed,
-- and a recompute would then give different answers depending on the order it
-- happened to run in.

insert into crests
    (code, name, description, category, scope, standing, award_mode, colour, sort_order)
values
('most_weekly_highs', 'Master of Storms',
 'Has topped the league''s scoring in more weeks than anyone.',
 'scoring', 'career', 'held', 'auto', 'gold-deep', 250),
('most_narrow_wins', 'The Fortunate',
 'Has won more games by under a point than anyone.',
 'scoring', 'career', 'held', 'auto', 'win', 260),
('most_narrow_losses', 'Hounded by Fate',
 'Has lost more games by under a point than anyone.',
 'scoring', 'career', 'held', 'auto', 'ink-muted', 270);

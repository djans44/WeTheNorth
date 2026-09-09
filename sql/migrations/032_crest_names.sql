-- Crest names in the league's own voice, and no crest for merely turning up.
--
-- Tenure crests are gone. "Founding member" and "Veteran" reward being there,
-- not doing anything, and a case full of them says less about a manager than
-- the seasons table already does.
--
-- 031 seeded plain names. These are the titles the league reads, so they are
-- written the way the rest of the site is written.

delete from owner_crests
 where crest_id in (select crest_id from crests
                     where code in ('founding_member', 'veteran'));
delete from crests where code in ('founding_member', 'veteran');

update crests set name = v.name from (values
    -- Held
    ('reigning_champion',    'Sitter of the Iron Throne'),
    ('fiercest_rival',       'Bane of Their Rival'),
    ('reigning_sacko',       'Lord of the Wastes'),

    -- Silverware
    ('champion',             'Crowned'),
    ('runner_up',            'Denied the Throne'),
    ('bronze',               'Third of Their Name'),
    ('regular_season_crown', 'Warden of the North'),
    ('sacko',                'The Forsaken'),
    ('dynasty',              'Twice Crowned'),

    -- Scoring
    ('weekly_high',          'Storm-Bringer'),
    ('season_high_points',   'The Bountiful'),
    ('points_against_king',  'The Besieged'),
    ('blowout',              'The Field of Fire'),
    ('nailbiter',            'Spared by Inches'),
    ('streak_five',          'The Unbroken'),
    ('gauntlet',             'None Were Spared'),

    -- Projection
    ('overachiever',         'Defier of Prophecy'),
    ('robbed',               'Undone by Fate'),

    -- Keepers
    ('four_year_man',        'The Long Watch'),
    ('triple_threat',        'Three Oaths Sworn'),
    ('cut_bait',             'Oathbreaker'),
    ('forfeit',              'The Watch Slept'),

    -- Transactions
    ('waiver_warrior',       'The Sellsword'),
    ('set_and_forget',       'Never Left the Keep'),
    ('wheeler_dealer',       'Master of Coin'),

    -- Rivalry
    ('rivalry_week_high',    'The Vengeful'),

    -- Honours, granted by hand
    ('best_team_name',       'Named in Song'),
    ('draft_day',            'Legend of the Choosing'),
    ('trade_of_year',        'The Bargain of the Age'),
    ('worst_beat',           'The Red Week')
) as v (code, name)
where crests.code = v.code;

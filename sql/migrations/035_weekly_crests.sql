-- Scoring splits in two: what a season decides, and what a week decides.
--
-- A season crest is a superlative -- the best week of the year, the biggest
-- rout of the year -- and cannot be known until the season is over. A weekly
-- crest is settled the moment a week's scores are entered and never changes
-- again, so it is awarded per week and a manager can hold many.
--
-- That needs a week on owner_crests, and it breaks the season index: two
-- weekly rows for the same manager and crest in the same season are correct.
-- The season index is narrowed to rows with no week, and a third index covers
-- the weekly ones.

alter table owner_crests add column week smallint null;

comment on column owner_crests.week is
    'Set only for a weekly crest. Null for season and career crests.';

drop index owner_crests_season_uniq;

create unique index owner_crests_season_uniq
    on owner_crests (owner_id, crest_id, season_year)
    where season_year is not null and week is null;

create unique index owner_crests_week_uniq
    on owner_crests (owner_id, crest_id, season_year, week)
    where week is not null;

-- The career index already covered rows with no season; make it explicit that
-- it means no week either, so a weekly row can never satisfy it.
drop index owner_crests_career_uniq;

create unique index owner_crests_career_uniq
    on owner_crests (owner_id, crest_id)
    where season_year is null and week is null;


-- The projection category folds away: those two crests are a season
-- superlative and a weekly event, and that is the division that matters.
update crests set category = 'scoring' where code = 'overachiever';
update crests set category = 'weekly'  where code = 'robbed';

-- Weekly, all of them settled by one week's scores.
update crests set category = 'weekly'
 where code in ('nailbiter', 'streak_five');

-- Storm-Bringer and The Field of Fire stay season-long: they are the best of
-- the year, not the best of a week. Their weekly counterparts are new.
insert into crests
    (code, name, description, category, scope, standing, award_mode, colour, sort_order)
values
('week_high', 'The Week''s Banner',
 'The highest score of the week.',
 'weekly', 'season', 'earned', 'auto', null, 2450),
('week_rout', 'Put to the Sword',
 'The largest margin of victory of the week.',
 'weekly', 'season', 'earned', 'auto', null, 2460),
('losing_streak', 'Winter Has Come',
 'Lost five regular season games in a row.',
 'weekly', 'season', 'earned', 'auto', null, 2520),
('lucky_win', 'Favoured by the Gods',
 'Won a game despite scoring under their own projection.',
 'weekly', 'season', 'earned', 'auto', null, 2530);

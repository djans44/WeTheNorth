-- Crests of honour: what a manager has won, and what they currently hold.
--
-- Two kinds, told apart by `standing`. An earned crest is a fact about the
-- past and never moves -- "Champion, 2025" stays David's whether or not he
-- wins again. A held crest is a fact about the present and passes to whoever
-- takes it next. The same underlying fact often produces both, which is a
-- title alongside a record rather than the same thing twice.
--
-- Only the auto tier is computed for now. award_mode and awarded_by are here
-- from the start so the commissioner grant flow needs no migration later.

create table crests (
    crest_id    integer generated always as identity primary key,
    code        text        not null unique,
    name        text        not null,
    description text        not null,
    -- silverware | scoring | projection | keepers | transactions
    -- | rivalry | tenure | honours
    category    text        not null,
    scope       text        not null check (scope in ('season', 'career')),
    standing    text        not null check (standing in ('earned', 'held')),
    award_mode  text        not null check (award_mode in ('auto', 'manual')),
    -- Ring and mark colour for a held crest; null for an earned one.
    colour      text        null,
    sort_order  integer     not null default 0,
    active      boolean     not null default true
);

comment on column crests.standing is
    'earned: kept forever, any number of holders. held: one at a time, rebuilt
     on recompute, rings its holder''s sigil.';

create table owner_crests (
    owner_crest_id integer     generated always as identity primary key,
    owner_id       integer     not null references owners (owner_id) on delete cascade,
    crest_id       integer     not null references crests (crest_id) on delete cascade,
    -- null for a career crest and for every held crest: "who holds it now"
    -- has no season to belong to.
    season_year    smallint    null references seasons (season_year),
    -- The number that earned it, for the hover: "182.40, week 6".
    detail         text        null,
    awarded_at     timestamptz not null default now(),
    -- null for auto-computed; set for a commissioner grant.
    awarded_by     integer     null references owners (owner_id)
);

-- Postgres treats nulls as distinct, so one unique constraint over
-- (owner, crest, season) would happily allow the same career crest twice.
-- Two partial indexes instead.
create unique index owner_crests_season_uniq
    on owner_crests (owner_id, crest_id, season_year)
    where season_year is not null;

create unique index owner_crests_career_uniq
    on owner_crests (owner_id, crest_id)
    where season_year is null;

create index owner_crests_owner on owner_crests (owner_id);
create index owner_crests_crest on owner_crests (crest_id);


-- ---- the catalogue -------------------------------------------------------
--
-- sort_order runs in hundreds so a crest can be slipped between two others
-- without renumbering the rest. For held crests it also decides which ring a
-- manager wears when they hold more than one.

insert into crests
    (code, name, description, category, scope, standing, award_mode, colour, sort_order)
values
-- Held: one manager at a time, ringing their sigil.
('reigning_champion', 'The reigning champion',
 'Won the most recent championship. Held until someone takes it.',
 'silverware', 'career', 'held', 'auto', 'gold', 100),
('fiercest_rival', 'Fiercest rival',
 'The best win rate against their own rival, over at least four meetings.',
 'rivalry', 'career', 'held', 'auto', 'bronze', 200),
('reigning_sacko', 'Holder of the Sacko',
 'Finished last in the most recent completed season.',
 'silverware', 'career', 'held', 'auto', 'oxblood', 300),

-- Silverware
('champion', 'Champion', 'Won the championship game.',
 'silverware', 'season', 'earned', 'auto', null, 1000),
('runner_up', 'Runner-up', 'Lost the championship game.',
 'silverware', 'season', 'earned', 'auto', null, 1100),
('bronze', 'Third place', 'Won the third-place game.',
 'silverware', 'season', 'earned', 'auto', null, 1200),
('regular_season_crown', 'Best in the North', 'Best record of the regular season.',
 'silverware', 'season', 'earned', 'auto', null, 1300),
('sacko', 'The Sacko', 'Finished twelfth.',
 'silverware', 'season', 'earned', 'auto', null, 1400),
('dynasty', 'Dynasty', 'Won the championship in consecutive seasons.',
 'silverware', 'career', 'earned', 'auto', null, 1500),

-- Scoring
('weekly_high', 'Mightiest week', 'The highest single-week score of the season.',
 'scoring', 'season', 'earned', 'auto', null, 2000),
('season_high_points', 'Points leader', 'Most points scored across the regular season.',
 'scoring', 'season', 'earned', 'auto', null, 2100),
('points_against_king', 'Cursed', 'Most points conceded across the regular season.',
 'scoring', 'season', 'earned', 'auto', null, 2200),
('blowout', 'Greatest rout', 'The largest margin of victory of the season.',
 'scoring', 'season', 'earned', 'auto', null, 2300),
('nailbiter', 'By a whisker', 'Won a game by less than a point.',
 'scoring', 'season', 'earned', 'auto', null, 2400),
('streak_five', 'Five straight', 'Won five regular season games in a row.',
 'scoring', 'season', 'earned', 'auto', null, 2500),
('gauntlet', 'Gauntlet', 'Beat every opponent faced in the regular season.',
 'scoring', 'season', 'earned', 'auto', null, 2600),

-- Projection
('overachiever', 'Defied the odds',
 'Beat the projection by more than anyone else that season.',
 'projection', 'season', 'earned', 'auto', null, 3000),
('robbed', 'Robbed', 'Lost a game they were projected to win by twenty or more.',
 'projection', 'season', 'earned', 'auto', null, 3100),

-- Keepers and contracts
('four_year_man', 'Four-year man', 'Held one player for four straight seasons.',
 'keepers', 'career', 'earned', 'auto', null, 4000),
('triple_threat', 'Fully committed', 'Held three contracted players at once.',
 'keepers', 'season', 'earned', 'auto', null, 4100),
('cut_bait', 'Cut bait', 'Voided a contract and took the defence pick.',
 'keepers', 'season', 'earned', 'auto', null, 4200),
('forfeit', 'Asleep at the wheel', 'Missed a keeper window and forfeited the slot.',
 'keepers', 'season', 'earned', 'auto', null, 4300),

-- Transactions
('waiver_warrior', 'Waiver warrior', 'Most waiver and free agent adds of the season.',
 'transactions', 'season', 'earned', 'auto', null, 5000),
('set_and_forget', 'Set and forget', 'Went a whole season without a single add.',
 'transactions', 'season', 'earned', 'auto', null, 5100),
('wheeler_dealer', 'Wheeler-dealer', 'Most trades of the season.',
 'transactions', 'season', 'earned', 'auto', null, 5200),

-- Rivalry
('rivalry_week_high', 'Lord of the grudge', 'The highest score of rivalry week.',
 'rivalry', 'season', 'earned', 'auto', null, 6000),

-- Tenure
('founding_member', 'Founding member', 'Played in the first season, 2022.',
 'tenure', 'career', 'earned', 'auto', null, 7000),
('veteran', 'Veteran', 'Played five seasons.',
 'tenure', 'career', 'earned', 'auto', null, 7100),

-- Honours: granted by the commissioner, never computed. Seeded so the grant
-- flow has something to grant when it is built.
('best_team_name', 'The finest name', 'Best team name of the season.',
 'honours', 'season', 'earned', 'manual', null, 8000),
('draft_day', 'Draft day legend', 'For conduct at the draft.',
 'honours', 'season', 'earned', 'manual', null, 8100),
('trade_of_year', 'Trade of the year', 'The deal everyone talked about.',
 'honours', 'season', 'earned', 'manual', null, 8200),
('worst_beat', 'The cruellest week', 'The defeat nobody deserved.',
 'honours', 'season', 'earned', 'manual', null, 8300);

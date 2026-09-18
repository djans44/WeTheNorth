-- The league votes on the four honours nobody can earn.
--
-- Named in Song, Legend of the Choosing, The Bargain of the Age, The Red
-- Week. No rule computes them because they are opinions, and the
-- commissioner has been the one writing them down -- the right mechanism
-- with the wrong decider, since twelve people have those opinions.
--
-- This does not touch owner_crests. A poll decides who should hold a crest;
-- awarding it is still the manual grant on /admin/crests, with awarded_by set
-- to whoever ran the poll. That keeps one way of holding a crest rather than
-- two, and it leaves the commissioner a tie to break.
--
-- One poll to a season, four questions on it. A manager opens the page once
-- at the end of a year and answers all four, because four separate asks of
-- twelve people is four chases and one is one.

begin;

create table crest_polls (
    poll_id     integer generated always as identity primary key,
    season_year smallint not null references seasons(season_year),
    opened_at   timestamptz not null default now(),
    opened_by   integer references owners(owner_id),
    closes_at   timestamptz not null,
    -- Set when the count is taken, whether that is the closing date arriving
    -- or the commissioner ending it early. Votes are hidden until then.
    closed_at   timestamptz,
    unique (season_year)
);

comment on table crest_polls is
    'One vote on the four manual crests, per season. Closing reveals the
     result; awarding is still a manual grant on /admin/crests.';

create table crest_votes (
    vote_id    integer generated always as identity primary key,
    poll_id    integer not null references crest_polls(poll_id) on delete cascade,
    crest_id   integer not null references crests(crest_id),
    -- Who voted, and who they voted for. Every candidate on every ballot
    -- resolves to exactly one owner, because a crest is held by a person:
    -- a team name belongs to its manager, a defeat to whoever suffered it,
    -- and each half of a trade is its own candidate.
    owner_id   integer not null references owners(owner_id),
    choice_owner_id integer not null references owners(owner_id),
    -- The ballot's own key for the candidate, so two votes for the same thing
    -- group exactly rather than by matching a label that might be built
    -- differently next time.
    candidate  text not null,
    -- The candidate as it was shown, which becomes owner_crests.detail when
    -- the crest is granted. Kept rather than rebuilt: the trade that won
    -- should read on the honour roll the way it read on the ballot.
    detail     text not null,
    cast_at    timestamptz not null default now(),
    -- One vote each per honour. Changing your mind updates this row rather
    -- than adding another, so the count is the row count.
    unique (poll_id, crest_id, owner_id)
);

create index crest_votes_tally on crest_votes (poll_id, crest_id, candidate);

comment on table crest_votes is
    'One row per manager per honour. Voting for a different candidate
     replaces the row; nothing records that someone changed their mind.';

commit;

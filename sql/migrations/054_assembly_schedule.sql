-- The assembly is scheduled, and a vote remembers what it was looking at.
--
-- Two things the first version could not do.
--
-- It opened the moment it was created. The plan is to open after the last
-- week of the regular season and rise on the Monday after the last night of
-- the playoffs, which means saying when rather than doing it by hand at the
-- right moment. opens_at defaults to now so nothing already open changes.
--
-- And the ballot grows while the assembly sits. Five of 2025's twenty-seven
-- Red Week candidates only exist after the playoffs -- including the second
-- closest defeat of the whole season, a semifinal lost by 2.08 -- and Legend
-- of the Choosing has no candidates at all until the final rosters are in,
-- because the test is that the player never changed hands all year.
--
-- ballot_seen records how many candidates an honour had when the vote was
-- cast, so the page can say "there are more now" without forcing anybody to
-- change their mind. A vote stands until its owner changes it.

begin;

alter table crest_polls
    add column opens_at timestamptz not null default now();

comment on column crest_polls.opens_at is
    'When the assembly sits. Before this it is scheduled and nobody can vote.';

alter table crest_votes
    add column ballot_seen smallint;

comment on column crest_votes.ballot_seen is
    'How many candidates this honour offered when the vote was cast. More
     than this now means the ballot has grown since, and the voter is told.';

commit;

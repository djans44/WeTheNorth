-- A week's account, kept wanted until it is written.
--
-- Generating one is a call to Gemini off the back of a score entry, and that
-- call fails often enough to matter: 503 "high demand" is common, and the
-- free tier's daily quota is a wall. Until now the only retries were three
-- inside app/summaries.py, eight and sixteen seconds apart, all of them on a
-- daemon thread and all of them over within half a minute. If Gemini was
-- having a bad afternoon the week simply had no account, and the only way
-- back was an admin noticing and pressing a button.
--
-- This is the record that outlives the process. Render's free tier spins the
-- app down when nobody is looking, so anything remembered in a dict is
-- remembered until the first quiet quarter of an hour; a row here is still
-- here afterwards, which is the whole point.
--
-- Weeks only. The season recap and the preview are written by an admin
-- standing in front of the page waiting for them, so there is nobody to
-- retry on behalf of.
create table summary_jobs (
    job_id        integer generated always as identity primary key,
    season_year   smallint not null references seasons(season_year),
    kind          text     not null default 'week',
    week          smallint not null,

    -- How many calls have been started for it, including the one that ran
    -- the moment the scores were saved. The first retry comes sooner than
    -- the rest, so the count is read and not merely counted up.
    attempts      smallint not null default 0,

    -- What went wrong last time, shown on the scores page. Null once it is
    -- written, so the page does not report a failure that has been overtaken.
    last_error    text,
    last_tried_at timestamptz,

    -- When the next attempt becomes due. Null means nothing more is coming:
    -- either it is written, or the night ran out, or the failure was the
    -- kind that repeating cannot fix.
    next_try_at   timestamptz,
    done_at       timestamptz,
    created_at    timestamptz not null default now(),

    constraint summary_jobs_kind_known check (kind in ('week')),
    constraint summary_jobs_one unique (season_year, kind, week)
);

-- The pinger asks one question -- what is due? -- every few minutes forever.
-- Partial, because a job that is finished or given up is most of the table
-- and never an answer.
create index summary_jobs_due on summary_jobs (next_try_at)
    where next_try_at is not null;

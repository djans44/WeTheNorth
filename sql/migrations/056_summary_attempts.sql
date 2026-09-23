-- One row per call to Gemini, kept after the fact.
--
-- summary_jobs answers "what is wanted and when is it next due". It cannot
-- answer "what happened", because it is one row with a running counter and
-- the latest error, and success wipes the error on the way out. On 23
-- September the previous day had to be reconstructed by inference: three
-- attempts, a stop at 15:53, a summary appearing at 23:34 with last_tried_at
-- still reading 15:53. The shape was recoverable. The error text that
-- stopped it was not, and neither was whether an attempt still running or
-- already failed -- last_tried_at is stamped when a job is claimed and
-- nothing marks the end, so a row mid-call and a row that failed look alike.
--
-- So: started and finished separately, the error kept rather than replaced,
-- and which of the three doors the attempt came through.
create table summary_attempts (
    attempt_id  integer generated always as identity primary key,
    season_year smallint not null references seasons(season_year),
    kind        text     not null default 'week',
    week        smallint not null,

    -- The job's attempt count when this one began. Null if there was no job,
    -- which a hand-written one that succeeds first time will not have.
    attempt_no  smallint,

    -- Which door: the clock, the scores page, the summaries page. Yesterday
    -- turned on telling a scheduled retry from a person pressing a button,
    -- and the only way to tell was that one of them stamps last_tried_at.
    source      text not null,

    started_at  timestamptz not null default now(),
    -- Null means it never came back: still running, or the process went away
    -- mid-call, which on this host is an ordinary Tuesday.
    finished_at timestamptz,
    outcome     text,
    error       text,

    -- What summaries.ask() decided about the failure, kept because the job's
    -- behaviour follows from it and "why did it stop" is the question.
    retryable   boolean,
    -- When a spent allowance said it would come back.
    resets_at   timestamptz,
    -- How many HTTP calls ask() spent on this attempt. The reason the daily
    -- allowance went faster than anyone expected: an attempt is not a call,
    -- it is up to ATTEMPTS of them. Null on success, where nothing reports it.
    calls       smallint,

    constraint summary_attempts_kind_known check (kind in ('week')),
    constraint summary_attempts_source_known
        check (source in ('schedule', 'scores', 'summaries')),
    constraint summary_attempts_outcome_known
        check (outcome is null or outcome in ('written', 'failed')),
    -- An outcome without an end, or an end without an outcome, would be a
    -- row that cannot be read. Either both or neither.
    constraint summary_attempts_finished_together
        check ((finished_at is null) = (outcome is null))
);

-- The question this table exists for is always about one week, newest first.
create index summary_attempts_target
    on summary_attempts (season_year, kind, week, started_at desc);

-- Generated prose about the league, of five kinds, in one table.
--
-- The kinds were settled before this was built, so all five are named here
-- even though two of them have no writer yet. Adding one later is then a
-- prompt and a row, not a migration against a live table:
--
--   preview   a season, written after keeper selection and before week 1
--   season    a season, written after week 17
--   week      a week: how the league stands after it, and what is next
--   matchup   one game. TBD -- needs player-level results, which the
--             database does not hold. matchups has team totals only.
--   chat      the league chat from Sunday morning to the end of Monday
--             night. TBD -- needs somewhere to read the chat from, which is
--             an ingest problem before it is a summary problem.
--
-- Append, never update. Every generation is kept and the newest is the one
-- displayed, which buys two things. A season summary is asked what changed
-- since its last version, and that version has to still exist to be handed
-- back to it. And a summary that comes out wrong can be regenerated without
-- destroying the evidence of what it replaced -- the same reason owner_crests
-- keeps every spell a title has ever been held for rather than the current
-- holder alone.
--
-- Nothing reads this table unless a row exists, so it ships empty and the
-- pages that show summaries simply show nothing until there is one.

create table summaries (
    summary_id   integer generated always as identity primary key,
    season_year  smallint not null references seasons(season_year),
    kind         text     not null,
    -- Which week, for the kinds that belong to one. Null for a season.
    week         smallint,
    -- Only a matchup summary names a game.
    matchup_id   integer  references matchups(matchup_id),
    body         text     not null,
    -- Which model wrote it. Recorded per row rather than assumed, because
    -- the prose will not read the same after the model is changed and the
    -- table will outlive several of them.
    model        text     not null,
    generated_at timestamptz not null default now(),

    constraint summaries_kind_known
        check (kind in ('preview', 'season', 'week', 'matchup', 'chat')),

    -- A season-wide summary has no week; everything else has one. Written as
    -- a check rather than left to the writer, because a week summary with a
    -- null week would be indistinguishable from a season summary on read.
    constraint summaries_week_matches_kind
        check ((kind in ('preview', 'season') and week is null)
            or (kind in ('week', 'matchup', 'chat') and week is not null)),

    constraint summaries_matchup_only_for_matchups
        check ((kind = 'matchup' and matchup_id is not null)
            or (kind <> 'matchup' and matchup_id is null))
);

-- The read is always "the newest of this kind for this target", so the index
-- carries the ordering rather than leaving it to a sort.
create index summaries_latest
    on summaries (season_year, kind, week, generated_at desc);

comment on table summaries is
    'Generated prose: one row per generation, newest wins. kind says what it '
    'is about and which of week and matchup_id apply.';

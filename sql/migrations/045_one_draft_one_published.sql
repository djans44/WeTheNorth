-- At most two summaries per thing written about: one draft, one published.
--
-- The table was append-only on purpose -- a regeneration became a new row
-- beside the one it hoped to replace, which is what made "generate again and
-- see" safe on a page the league reads. Writing the 2025 backlog showed what
-- that costs in practice: seventeen weeks took forty-seven rows to reach,
-- twenty-two of them superseded within the hour and none of them ever wanted
-- again. Nobody has asked to read a version that was replaced.
--
-- So the rule is now two at a time. Writing a draft replaces the draft that
-- was there; publishing one replaces whatever was published. The safety that
-- mattered is untouched -- a draft still cannot be seen by the league, and
-- publishing is still a separate deliberate act.
--
-- Worth being plain about the cost: publishing over a published summary
-- destroys the text that was there. It is not recoverable from this table.

begin;

-- Keep the newest of each state for each thing written about, drop the rest.
delete from summaries s
using summaries newer
where s.season_year = newer.season_year
  and s.kind = newer.kind
  and s.week is not distinct from newer.week
  and s.matchup_id is not distinct from newer.matchup_id
  and (s.published_at is null) = (newer.published_at is null)
  and (coalesce(newer.published_at, newer.generated_at),
       newer.summary_id)
    > (coalesce(s.published_at, s.generated_at), s.summary_id);

-- And make the rule structural rather than remembered. nulls not distinct so
-- that a preview (week null, matchup_id null) collides with another preview
-- for the same season; without it every null is unique and the index allows
-- exactly what it is meant to forbid.
create unique index summaries_one_draft
    on summaries (season_year, kind, week, matchup_id)
    nulls not distinct
    where published_at is null;

create unique index summaries_one_published
    on summaries (season_year, kind, week, matchup_id)
    nulls not distinct
    where published_at is not null;

commit;

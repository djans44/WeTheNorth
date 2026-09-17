-- A summary is written before anyone has read it, and the two are not the
-- same event.
--
-- The first preview generated for 2026 said Josh led the league in points
-- (Tulio did) and turned "Carter kept nobody in 3 keeper rounds" into "Carter
-- yields his third-round pick". Both were caused by how the facts were
-- phrased and both are fixed, but they were only caught by querying the
-- database afterwards. Prose generated from data is not self-evidently right,
-- and the league reads this page.
--
-- So generating and publishing are separated. A row appears unpublished, the
-- commissioner reads it, and publishing is a second deliberate act. The
-- season page reads only published rows, so an unread draft is invisible
-- rather than wrong in public.
--
-- Null rather than a boolean: when it was published is worth knowing, and
-- "not yet" is the absence of a time rather than a false.

alter table summaries add column published_at timestamptz;

-- The read is "newest published of this kind", so the index that serves it
-- has to know about the column. The old one stays for the admin view, which
-- wants the newest row whether published or not.
create index summaries_published
    on summaries (season_year, kind, week, published_at desc)
    where published_at is not null;

comment on column summaries.published_at is
    'When a human approved it. Null means written but not shown: the season '
    'page reads published rows only.';

# Backlog

What is agreed but not built. `docs/PROJECT.md` section 9 describes gaps in
what exists; this is the queue of work.

Each numbered item is one change: build it, show it, approve it, commit it,
then start the next. Nothing here is in flight.

Done and removed: the player-scoring note, now
`docs/features/player-scoring.md`; the `/draft-order` audit and everything it
turned up, twelve commits; the `/draft-prep` audit, eight; the `/keepers`
audit, four; the `/rules` audit, seven. All four pages are finished.

Also done since, and left numbered here so the audit list keeps its numbers:
**1 weekly summaries** and **2 season summaries** are built, generated for
2022-2025 and published, with an admin page at `/admin/summaries` and at most
one draft and one published version of each (migration 045). **5
`/admin/scores`** and **11 `/admin/schedule`** have had their audits -- ten
findings and six respectively, two of which were losing data.

Two things came out of `/draft-prep` that belong to the whole site rather than
to that page: the connection pool, written up in `PROJECT.md`, and per-season
sigil rings on both draft pages. The admin pages that carry a season picker
still wear today's crowns on an old season -- worth taking under each of their
own audits below.

One thing came out of `/keepers` that the eight audits left should know: the
finding this list had recorded -- that neither keeper table was in a
`.scroller` and both pushed the page sideways -- was wrong on both counts, and
the real fault was a cut last column, which hid a control. `PROJECT.md`
section 10 now carries what to look for instead, and how to measure it.
`/rules` had the same fault in its playoff bracket, found by looking for it.

Two things came out of `/rules` that the admin audits inherit. The three
selection steps are **keeper rounds** everywhere an owner can read, matching
what `/keepers` calls them, but `/admin/keepers` and `/admin/keepers/edit`
still say Phase in a column header and several notes -- settle that under
their own audits rather than half-changing it from elsewhere. And the page
carries the seven-entry `.sectionnav` rail: it does not survive more than
about seven entries, because below 74rem it becomes a strip and each entry
gets a share of 375px.

Also done and not from this list: **Keeper results**, a new page at
`/keepers/results`. Who was kept, by manager and by keeper round, for any
season. A finished season's rounds are inferred from the record by the
league's own rule, because `keeper_selections` never stored which round
settled a pick; the season being chosen reads its contracts from
`keeper_phase_plan` and fills in as an admin resolves each round. Keepers
became two nav items with it -- selection and results.

---

## 1. Weekly summaries

Prose recapping a week, generated and stored, shown in that week's panel on
the season page.

Pairs with the season summaries below and shares everything with them: the
same table, the same generator, the same key. A weekly summary is about
**events** — rivalry meetings, rematches, margins, who moved into or out of
playoff position — where a season summary is about **state**, the arc across
the standings. That distinction was settled when the season page was
planned and is the reason they are two prompts rather than one.

Blocked on the same `GEMINI_API_KEY` as the season summaries, so do them
together.

## 2. Season summaries

Carried over from the season-page plan; the only piece of it never built.

- Migration for `season_summaries` — **renumber to the next free number**.
  The plan says `031`, which the crests table took long ago.
- `scripts/write_summaries.py`, hand-run, dry by default like
  `resolve_phase.py`. The app only ever reads the table.
- Six to generate first: recaps for 2022–25, the week 17 summary for 2025,
  and a 2026 forecast. The script picks its shape from the data — a season
  with results gets a recap, one without gets a forecast, one in progress
  gets the "what changed" treatment with its previous summary supplied.
- The season summary takes **its own previous version** as input and is asked
  what changed. Without that it restates itself almost verbatim through the
  middle of a season and reads as though nothing happened.

Needs `GEMINI_API_KEY`. Nothing generates without it and nothing else on the
site depends on it, so everything still renders while it is missing.

## 3. Season calendar

A page for the shape of a league year: when keeper windows open and close,
the draft, week one, the trade deadline, the playoff weeks.

Overlaps the create-season checklist in `docs/features/season-setup.md` —
that is the *doing*, this is the *seeing*. Worth settling which owns the
dates before building either, because `keeper_windows` is the only table
that holds any today.

## 4. Admin imports from text files

One import per data element, so a season can be stood up or repaired without
hand-written SQL. This is the answer to the rough edge in PROJECT.md §9:
`seasons` and `teams` have no UI at all and have only ever been written by
migrations.

Paste or upload, preview what it will do, then apply. Never a silent write.

Two are built and live, both on that pattern and both worth copying for the
rest: `/admin/transactions` and `/admin/draft`. What they settled --

- the parser is a module of its own, takes text, touches no database, and
  reports what it could not read rather than guessing;
- the preview is a separate press from the write, and carries the paste
  through in a hidden field so what is saved is what was read;
- a tickbox appears only for a thing the page has just told you about;
- Yahoo prints the same data more than one way, and a loader that knows only
  the shape it was written against will meet the other one.

| Element | Table | Notes |
|---|---|---|
| Seasons | `seasons` | The row every other import needs first |
| Teams | `teams` | One per owner per season; team names change yearly |
| Owners | `owners` | Emails are the credential and are deliberately not in git |
| Players | `players` | ~359 rows. **No Yahoo id** — `player_id` is a local identity column and every importer matches on `lower(full_name)`. See `docs/features/player-scoring.md` |
| ~~Draft picks~~ | `draft_picks` | **Done** -- `/admin/draft`. Reads both board shapes; the keeper badge is a private-use glyph and has to be read before the icons are stripped |
| Rosters | `rosters` | End-of-season snapshot; what keepers are drawn from |
| ~~Transactions~~ | `transactions` | **Done** -- `/admin/transactions`. A week at a time, idempotent on a natural key, and it says so when a paste may have a gap |
| Matchups | `matchups` | Score entry already exists; a bulk import is for a season's history |
| ADP | `player_adp` | Locked at signing for 3-year contract pricing |
| Keeper selections | `keeper_selections` | ~104 rows |

Order matters: seasons, then teams, then players, then everything keyed on
them. An import that runs out of order should say what is missing rather
than fail on a foreign key.

---

**Items 5 to 12 are the UI audits.** The same treatment `/history`, `/season` and `/team` were given: read the
page, say what is wrong with it, agree the changes, build them. **Each is its
own item** — one audit, one conversation, one commit. Eight left of
the twelve; `/draft-order`, `/draft-prep`, `/keepers` and `/rules` are done.

A design and usability audit rather than form validation. `/rules` was on
the list and has no inputs at all, which settled which is meant.

None of the eight left has ever been looked at this way. The four that have
were each worth a handful of real changes, so expect the same here.

## 5. Audit `/admin/scores`

Entering a week. Saving replaces every matchup for that week, and it now
recomputes the crests as well.

The one admin page used every week during a season, so it earns the most
polish.

## 6. Audit `/admin/keepers`

Windows, review and phase resolution in one page. Approving and rejecting
submissions, and running a resolution that turns plans into submissions.

Rejecting deletes and reopens rather than labelling, which is worth checking
reads clearly, because it is destructive and does not look it.

## 7. Audit `/admin/keepers/edit/{sid}`

Overriding one submission. Notably, an admin override deliberately does not
validate a round against the manager's other phases, so a duplicate round can
be created — visible in the Settled table, blocked by nothing. PROJECT.md §9
records this; the audit should decide whether the page says so.

## 8. Audit `/admin/owners`

Team name, colour and initials for everyone in one pass.

## 9. Audit `/admin/owners/{oid}`

One owner, in detail. The only page with **no lede and no h1** worth the
name, which is where the audit starts.

Emails are edited here and they are the sign-in credential, so whatever it
does with them matters more than it looks.

## 10. Audit `/admin/rivals`

Generated pairings with a preview, and a manual override validated for mutual
pairings.

## 11. Audit `/admin/schedule`

The generator: fourteen weeks, three opponents twice and the rest once,
rivalry week pinned, no pair in consecutive weeks. Saves into `matchups` with
null scores so score entry pre-fills.

Destructive on save and the results are hard to eyeball, which is the thing
to look at.

## 12. Audit `/admin/crests`

The grant flow, built last. Worth an audit precisely because it is new and
was never looked at with fresh eyes.

## 13. A roster tracked all year, not snapshotted at the end

Adds, drops and trades together are a complete record of who moved where, so
a roster does not have to be a thing that arrives once in January. Start from
the draft, apply every transaction in order, and the roster is known on any
date of the season. That turns the end-of-season load from the source of
truth into a **check** on it: import the final rosters, compare against what
the transactions say, and anything that disagrees is a gap in the record
worth finding before keeper selection runs off it.

Worth doing in that order, because each piece is useful before the next
exists:

1. **Show the transactions.** They have been loaded since 2025 and are
   displayed nowhere. A manager's page and a league-wide log, newest first.
   The data is already there; this is a page, not a pipeline. **Next up** --
   both loaders below are now built, so nothing blocks this.
2. **Derive the current roster.** Draft picks plus transactions up to a date.
   Needs the draft loaded for the season, which 2026 does not have yet --
   see the loader below. Shown per team, and it is the thing that makes a
   mid-season site feel live rather than historical.
3. **Validate the final rosters against it.** The end-of-year import stops
   overwriting and starts reconciling: here is what the record says the
   roster is, here is what Yahoo says, here is the difference. Keeper cost
   basis reads `transactions` directly (`keeper_cost_basis`, migration 023),
   so a hole in the transaction record is a wrong keeper price -- this is
   the check that catches it before anyone selects.

Loaders for the two inputs:

- **Draft results.** Built, and 2026 is loaded. This was the blocker on
  step 2 and it is gone.
- **Rosters.** Still a row in item 4. `rosters` is an end-of-season
  snapshot, 2025 only -- and under this item it stops being the source of
  truth and becomes the thing step 3 reconciles against.

## 14. Let the league vote on the four honours

Named in Song, Legend of the Choosing, The Bargain of the Age, The Red Week.
The commissioner is the right mechanism but the wrong decider — they are
opinions and twelve people have them.

A poll would fit the existing schema without touching `owner_crests`: one
round of nominations, one of votes, a closing date, and the winner written
as an ordinary manual grant with `awarded_by` set to whoever ran it.
`/admin/crests` stays as the fallback and as the thing a poll ultimately
calls.

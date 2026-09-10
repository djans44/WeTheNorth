# Backlog

What is agreed but not built. `docs/PROJECT.md` section 9 describes gaps in
what exists; this is the queue of work.

Each numbered item is one change: build it, show it, approve it, commit it,
then start the next. Nothing here is in flight.

Done and removed: the player-scoring note, now
`docs/features/player-scoring.md`.

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

| Element | Table | Notes |
|---|---|---|
| Seasons | `seasons` | The row every other import needs first |
| Teams | `teams` | One per owner per season; team names change yearly |
| Owners | `owners` | Emails are the credential and are deliberately not in git |
| Players | `players` | ~359 rows. **No Yahoo id** — `player_id` is a local identity column and every importer matches on `lower(full_name)`. See `docs/features/player-scoring.md` |
| Draft picks | `draft_picks` | ~624 rows; the source of every keeper cost basis |
| Rosters | `rosters` | End-of-season snapshot; what keepers are drawn from |
| Transactions | `transactions` | ~301 rows; adds, drops, trades. Cost basis depends on these |
| Matchups | `matchups` | Score entry already exists; a bulk import is for a season's history |
| ADP | `player_adp` | Locked at signing for 3-year contract pricing |
| Keeper selections | `keeper_selections` | ~104 rows |

Order matters: seasons, then teams, then players, then everything keyed on
them. An import that runs out of order should say what is missing rather
than fail on a foreign key.

## 5. UI audits, one page each

The same treatment `/history`, `/season` and `/team` were given. Each of
these is **its own action** — audit, agree the changes, build, commit — not
one sweep.

Read as a design and usability audit rather than form validation: `/rules`
has no inputs, and it is on the list.

**Draft**
1. `/draft-order`
2. `/draft-prep`

**Keepers**
3. `/keepers`

**Rules**
4. `/rules`

**Admin**
5. `/admin/scores`
6. `/admin/keepers`
7. `/admin/keepers/edit/{sid}`
8. `/admin/owners`
9. `/admin/owners/{oid}`
10. `/admin/rivals`
11. `/admin/schedule`
12. `/admin/crests`

## 6. Let the league vote on the four honours

Named in Song, Legend of the Choosing, The Bargain of the Age, The Red Week.
The commissioner is the right mechanism but the wrong decider — they are
opinions and twelve people have them.

A poll would fit the existing schema without touching `owner_crests`: one
round of nominations, one of votes, a closing date, and the winner written
as an ordinary manual grant with `awarded_by` set to whoever ran it.
`/admin/crests` stays as the fallback and as the thing a poll ultimately
calls.

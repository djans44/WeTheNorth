# Backlog

What is agreed but not built. `docs/PROJECT.md` section 9 describes gaps in
what exists; this is the queue of work.

Each numbered item is one change: build it, show it, approve it, commit it,
then start the next. Nothing here is in flight.

Done and removed: the player-scoring note, now
`docs/features/player-scoring.md`; the `/draft-order` audit, which shipped as
ten commits ending `9151699`. What that audit found and did not fix is item 5
below, in the slot the audit itself used to occupy.

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

---

**Items 6 to 16 are the UI audits.** The same treatment `/history`, `/season` and `/team` were given: read the
page, say what is wrong with it, agree the changes, build them. **Each is its
own item** — one audit, one conversation, one commit. Eleven left of the
twelve; `/draft-order` is done.

A design and usability audit rather than form validation. `/rules` has no
inputs and is on the list, which settles which is meant.

None of them has ever been looked at this way. The three pages that have were
each worth a handful of real changes, so expect the same here.

## 5. `/draft-order` does not live-update

Twelve people watch this page during the one session where the order is
chosen. Nothing on it refreshes. Whoever is on the clock picks and the other
eleven keep looking at a frozen page until they reload — including the person
who is next, who has no way to know their turn has come.

Found by the audit and left alone on purpose: everything else that audit
changed was layout, wording and one route guard, and this is the only thing
in it that needs a mechanism.

Cheapest first. A `<meta refresh>` while a draft is under way is two lines
and reloads the whole page every few seconds, losing the reader's scroll
position and any half-made choice. Polling a small JSON endpoint and
re-rendering the board and the on-the-clock card from `app.js` keeps both and
is perhaps forty lines. Server-sent events are the right shape and the wrong
host: Render's free tier idles and cold-starts, which a held-open connection
fights.

Three smaller things the same audit turned up and did not take:

- The Slot column says "waiting" for everyone without a slot, whether their
  turn has passed or has not yet come. Those are different states.
- `me` is in the template context and nothing uses it, so there is no way to
  find yourself among the twelve.
- Nothing on the page says the draft is a snake. `/draft-prep` draws it; a
  manager choosing slot 1 or slot 12 is choosing on that basis and this page
  never mentions it.

## 6. Audit `/draft-prep`

The board: snake order, pick numbers that skip keeper cells, void penalty
rounds counting as taken so keepers bump around them.

The densest grid on the site and the one most likely to be read on a phone at
the draft.

## 7. Audit `/keepers`

Keeper selection. Three sequential phases, one pick each, contracts filling
the earliest phases automatically, plans, voids, and a submit per phase.

The most intricate rules in the league meet the page most owners use least
often, which is the hard combination.

`.picker-table tr.picked` is dead: nothing in the templates, `app.js` or
`main.py` ever sets `picked`, so the highlight on the row you chose has never
appeared. Found while auditing `/draft-order`, which was carrying the same
class for the same reason.

## 8. Audit `/rules`

Eleven sections now, and it grew a crests section without anyone looking at
the whole. Read-only, so this is purely about whether it can be read.

## 9. Audit `/admin/scores`

Entering a week. Saving replaces every matchup for that week, and it now
recomputes the crests as well.

The one admin page used every week during a season, so it earns the most
polish.

## 10. Audit `/admin/keepers`

Windows, review and phase resolution in one page. Approving and rejecting
submissions, and running a resolution that turns plans into submissions.

Rejecting deletes and reopens rather than labelling, which is worth checking
reads clearly, because it is destructive and does not look it.

## 11. Audit `/admin/keepers/edit/{sid}`

Overriding one submission. Notably, an admin override deliberately does not
validate a round against the manager's other phases, so a duplicate round can
be created — visible in the Settled table, blocked by nothing. PROJECT.md §9
records this; the audit should decide whether the page says so.

## 12. Audit `/admin/owners`

Team name, colour and initials for everyone in one pass.

## 13. Audit `/admin/owners/{oid}`

One owner, in detail. The only page with **no lede and no h1** worth the
name, which is where the audit starts.

Emails are edited here and they are the sign-in credential, so whatever it
does with them matters more than it looks.

## 14. Audit `/admin/rivals`

Generated pairings with a preview, and a manual override validated for mutual
pairings.

## 15. Audit `/admin/schedule`

The generator: fourteen weeks, three opponents twice and the rest once,
rivalry week pinned, no pair in consecutive weeks. Saves into `matchups` with
null scores so score entry pre-fills.

Destructive on save and the results are hard to eyeball, which is the thing
to look at.

## 16. Audit `/admin/crests`

The grant flow, built last. Worth an audit precisely because it is new and
was never looked at with fresh eyes.

## 17. Let the league vote on the four honours

Named in Song, Legend of the Choosing, The Bargain of the Age, The Red Week.
The commissioner is the right mechanism but the wrong decider — they are
opinions and twelve people have them.

A poll would fit the existing schema without touching `owner_crests`: one
round of nominations, one of votes, a closing date, and the winner written
as an ordinary manual grant with `awarded_by` set to whoever ran it.
`/admin/crests` stays as the fallback and as the thing a poll ultimately
calls.

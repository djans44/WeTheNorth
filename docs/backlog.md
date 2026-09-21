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
`/admin/scores`**, **6 `/admin/keepers`** and **11 `/admin/schedule`** have
had their audits -- ten findings, seven and six respectively, three of which
were losing data. **13 the year-round roster** is complete to its last step,
and **14 the assembly** is built and the league is voting in it.

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
| ~~Rosters~~ | `rosters` | **Done** -- `/admin/rosters`. Paste all twelve; it reconciles them against the draft and every move since before it writes, which is step 3 of item 13 |
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
own item** — one audit, one conversation. One left of the twelve: **10
`/admin/rivals`**, plus the fresh-eyes pass on 12.

A design and usability audit rather than form validation. `/rules` was on
the list and has no inputs at all, which settled which is meant.

Every one looked at this way has been worth a handful of real changes, and
several have been worth a data-losing bug. Expect the same of the last one.

## 5. Audit `/admin/scores`

Entering a week. Saving replaces every matchup for that week, and it now
recomputes the crests as well.

The one admin page used every week during a season, so it earns the most
polish.

## 6. ~~Audit `/admin/keepers`~~ **Done**

Seven findings, five commits. The three that mattered: "Reset this manager"
reset the whole season when the manager list was empty, rejecting deleted a
submission with nothing in front of it, and saving windows accepted a
half-filled row, a closes-before-opens, and a resolved round's dates while
always saying "Windows saved".

It also turned up one that was not this page's: `data-confirm` was bound to
`form[data-confirm]` and four of the five confirms on the site are written on
the button, so the assembly rising, the results being proclaimed, a summary
removed and a keeper round forfeited all asked nothing. Fixed in `app.js`.

The Phase-versus-keeper-round naming the `/rules` audit left for this page is
settled: Round in every column header, Cost for the column that was a second
Round.

## 7. ~~Audit `/admin/keepers/edit/{sid}`~~ **Done**

Eight findings, one commit. Every value went to the database unchecked, so a
contract of seven years, a round of 99 and a player id that does not exist
each came back as an unhandled 500 -- the constraints held, the admin just
had no idea what had happened.

Worse: the player id was taken on trust, so another manager's keeper saved
happily onto a submission. An override kept calling itself the manager's
plan, so Settled credited them with a pick they never made. And the season
came from a hidden field, so a mismatched one updated nothing and still
reported success.

The duplicate-round question this item asked is settled: it is still allowed,
which is the point of an override, but the page names the clash -- "R1 is
already Keeper Round 2" -- rather than carrying a standing caveat that it is
not checked.

**A note on measuring.** This page was 412px wide on a 390, 375 and 360px
viewport and exactly 420 on a 420 one, which is the width these audits are
habitually probed at. One measurement said it was fine. Check more than one.

## 8. ~~Audit `/admin/owners`~~ **Done**

Six findings, four commits. The one that mattered: both importers send an
admin here to rename a team when a pasted name does not match, and the roster
loader reconciles any year while this page could only rename the newest -- so
the instruction was a dead end for every season but this one, and nothing
else in the site renames a past season's team. The page takes a year now and
the importers link to the one they are loading.

Also: the colour picker's hidden radios are position:absolute inside a label
that is only display:flex, so they resolved against the initial containing
block and left the table, scrolling a 420px page out to 560. That rule is
shared, so the fix is too. And a colour nobody else holds is drawn as a ring
-- thirteen rows of sixteen swatches is not something anyone maps by eye, and
the tooltip that used to be the only answer does not exist on a phone.

## 9. ~~Audit `/admin/owners/{oid}`~~ **Done**

Seven findings, two commits. Three of them were ways to lock somebody out.

Unticking Admin on your own record saved and updated the live session at
once, so the redirect landed on a page you could no longer reach and only the
other admin could undo it -- the last-admin guard never caught it, because
with two admins there is always another one. Refused now, with retiring
yourself.

The email really is the credential: a straight match, no password, no reset.
`type="email"` was the only check, which is none at all on a post that did
not come from a browser, so a value that can never reach an inbox saved
happily and the manager simply could not get in. Both this page and the add
form check the shape now.

And the note under it read "which is Tulio's situation today". His address
was filled in at some point and the sentence had been wrong ever since --
which is what naming a person in explanatory copy buys you. It reads the roll
instead.

The rest was shape: the season round-trips from the list and back, the save
says what it changed, the way back sits above the title, and the initials box
shows what it would derive.

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

**Mostly overtaken.** Item 14 rebuilt this page as Assembly admin: a year to
pick, the five steps in the order they happen, results that stay hidden until
the assembly rises, and manual granting narrowed to settling a tie. That was
a rework to a brief rather than an audit, so what is left is the fresh-eyes
pass over what it became -- smaller than the other four, and worth doing last.

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

1. ~~**Show the transactions.**~~ **Done** -- `/transactions`, under History.
   The whole season newest first, an add and the drop that made room for it
   read as one move, and a filter strip for one manager.
2. ~~**Derive the current roster.**~~ **Done** -- `/rosters`, its own nav
   item. From 2026 it is the draft with every move since applied to it;
   2022-2025 cannot be derived, because their transactions were loaded as
   adds with no drops against them, so those show the stored snapshot and
   the page says which of the two it is. One squad at a time as well as all
   twelve.

   Worth carrying into step 3: **roster size proves nothing.** IR slots mean
   thirteen to fifteen are all legitimate, so a squad being a player over is
   not evidence of a missing move. A size check was built, cried wolf over
   six ordinary 2026 rosters, and was taken out again.
3. ~~**Validate the final rosters against it.**~~ **Done** -- `/admin/rosters`.
   Paste all twelve and it says, per manager, who is on the roster and not in
   the record and who is in the record and not on the roster, before it
   writes anything. 2025 reconciles clean, which is the draft import, the
   transaction import, the roster parser and the derivation all agreeing at
   once. Keeper cost basis reads `transactions` directly
   (`keeper_cost_basis`, migration 023), so a hole in the transaction record
   is a wrong keeper price, and this is the check that catches it before
   anyone selects.

**Item 13 is finished.** Both loaders are built: draft results, with 2026
loaded, and rosters. `rosters` has stopped being the source of truth and is
now the thing the record is checked against.

## 14. ~~Let the league vote on the four honours~~ **Done**

The Assembly: its own nav heading, open deliberations and proclamations, a
badge counting the assemblies sitting that a manager has not spoken at, and
Assembly admin to call one, watch the ballot, rise it and proclaim what it
decided. No nomination round in the end -- the candidates are already known,
so the ballot is built from the season: the twelve team names, each side of
each trade, every defeat by ten points or fewer, and every late pick still
held at the close.

Two of the four ballots grow while the assembly sits, so a vote records how
many candidates it was cast against; a manager is told when there are more to
look at and is never made to change their mind, and a vote for a candidate
that has since left the ballot becomes an abstention rather than counting for
somebody who is no longer eligible. Winners are still written as ordinary
manual grants with `awarded_by` set, and a tie is reported for the
commissioner to settle rather than broken by the count.

Four assemblies are sitting now, one per season 2022-2025.

## 15. The last week's account is written and shown nowhere

A week's account appears on the *following* week's panel -- the banner reads
`week_summaries.get(shown - 1)` under "Thus passed week N". The last week of
a season has no following week, so 2025's week 17 account exists, is
published, and renders on no page at all. Checked: it is on none of weeks
14-17 or the season view.

The season view has its own season summary in that slot, so it is not simply
a matter of putting it there. Worth deciding where the last word of a year
belongs -- probably under the final week itself, which would make the banner
"the week just gone" for every week except the last, where it is this one.

## 16. Injuries and trades in the weekly account

**Parked, deliberately.** Too much of it rides on one person typing the right
thing on a Tuesday, and the half worth having is the half that is not built
yet: a tie-in to weekly lineups, so most of it plumbs itself.

The idea: a week's account should be able to call out injuries that bear on
what comes next, and name any trade that went down. Neither is in the facts
the generator gets today -- `week_facts` has results, superlatives, the
table, movement, runs, crests, titles and next week's fixtures, and nothing
about a player beyond a name.

### What was worked out before it was parked

**The player to manager lookup needs no new data.** Given a player name, who
holds him comes straight off the draft plus every move since -- the same
derivation `/rosters` uses. Checked against three 2026 players; all three
resolved to the right manager with the right arrival.

**Trades are blocked on something else entirely.** `matchups` has no date,
`seasons` has no start date, and `transactions.occurred_on` is a bare date,
so **nothing in the schema can say which week a trade fell in**. 61 trades
across 2022-25, clustered late September to mid-November. The smallest fix is
one date per season -- `week_one_starts` -- and arithmetic, NFL weeks being
exactly seven days apart. That is really the first brick of item 3, and it
pays for itself elsewhere: the transactions page could group by week, and the
import could say which weeks a paste covers.

**Storing injuries, if it is ever done by hand:** one row per player per week,
with the holding `team_id` snapshotted at entry. Not a span with an end week
-- an unclosed span says a player is hurt forever and nobody notices until
December. Snapshotting the holder also sidesteps the date-to-week wall: asking
in week 10 who held him in week 5 would otherwise mean replaying moves against
a date.

**Three things that will bite whoever builds it.**

- *When* in the week matters and changes the meaning completely. Hurt in
  Sunday's game and the score already reflects it; hurt on Monday night and
  the score is clean and the injury belongs wholly to the week ahead. Same
  row, opposite readings.
- The model invents causation. Handed "Chris lost" and "Chris's quarterback
  got hurt" it will write that the first was because of the second, which may
  be flatly false -- and **we cannot know whether an injured player was even
  started**, because there are no weekly lineups. See
  `docs/features/player-scoring.md`. The prompt has to say so or the accounts
  fill up with plausible fiction.
- Duration is unknown on the Tuesday the account is written, and that is
  fine: a weekly account is a contemporaneous document and should read as
  written when it was. Where the duration is not known, the instruction that
  works is to write it as a question the week leaves open rather than as a
  fact about the future. Regenerating a published week later, once more is
  known, was considered and rejected -- it makes the archive dishonest.

### The prompt block, drafted and not used

Anything not drawn from the record needs introducing as such, ahead of the
facts rather than as a postscript, and the last sentence is the one that
does the work:

> The commissioner's note on this week. This is the one part of what follows
> that is not drawn from the league's record -- it is what somebody who
> watched the week thinks matters and the tables cannot show. Weigh it as you
> would any other material: use what earns a place in the account and leave
> what does not. Do not quote it, do not work through it, and do not invent
> detail around it. If it says a player is hurt, you know that he is hurt and
> you know nothing else -- not how long for, not what it means for a season,
> unless the note says so itself.

### What unblocks the version worth building

Per-player scoring and weekly lineups, which is
`docs/features/player-scoring.md` -- not built, and that note says what it
would take. With lineups, an injury stops being something typed in and
becomes something the record shows: who was started, what they scored, and
what a manager lost. That is the version where this earns its place.


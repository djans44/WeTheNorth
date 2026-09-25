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
one draft and one published version of each (migration 045). **13 the
year-round roster** is complete to its last step, and **14 the assembly** is
built and the league is voting in it.

Items 1, 2 and 13 are the three whose own sections still read as plans. What
1 and 2 describe was largely built another way -- there is no
`scripts/write_summaries.py`, because an admin page turned out to be the right
shape -- so those sections are a record of what was intended rather than of
what exists. Worth rewriting when somebody next has reason to read them.

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

## 3. ~~Season calendar~~ **Done, bar one check**

A page for the shape of a league year: when keeper windows open and close,
the draft, week one, the trade deadline, the playoff weeks.

Overlaps the create-season checklist in `docs/features/season-setup.md` —
that is the *doing*, this is the *seeing*.

**The ownership question is settled, and it mostly dissolved.** Listing the
dates put them in three groups rather than one:

- **Windows that gate a form stay where they are.** `keeper_windows` and
  `crest_polls` hold `opens_at`/`closes_at` and both are *enforcement* --
  `poll_state()` is the single rule for assembly state, and keeper submission
  is refused outside its window. Moving them into a calendar table means the
  enforcement reads a different table: churn on the most intricate part of
  the domain for nothing. The page reads them.
- **Derivable dates are never stored.** The playoff weeks are 15, 16 and 17
  every year, and given week one every week's dates are arithmetic. Storing
  them is a second place to be wrong.
- **Three dates were genuinely unowned:** week one, the draft, the trade
  deadline.

**Week one is built** -- `seasons.week_one_sunday`, migration 057, with a
`league_week(season, date)` function. The Sunday rather than the Tuesday the
fantasy week opens: checkable against any NFL schedule, and unambiguous where
"the Tuesday" invites the wrong side of the weekend and shifts the year by
five days. A week runs Tuesday-before to Monday-after.

**Both dates are set on `/admin/season-setup`**, step one, and stay editable
after creation -- which nothing else in step one is, because a team count is
structural while a deadline is a decision the league can revise. Week one is
offered pre-filled from the Labor Day pattern, so it is a confirmation rather
than a lookup, and four refusals guard it, of which the one that earns its
keep is "that is a Tuesday": it passes every other kind of validation and then
moves the whole year by five days.

The deadline is a **date**, not a week. A week would derive itself from the
anchor every year with nothing to re-enter, and the record even suggests which
one -- of 63 trade sides the latest falls in week 11 -- but the league sets it
by hand, and a stored week would be the app telling the league what its own
rule is.

**The page is built**: `/calendar`, under League. Public, because it exists so
that nobody can say they did not know a keeper window had opened. It owns
nothing -- the windows and the assembly are read from the tables that enforce
them -- and what it adds is the arithmetic. Rivalry week and the playoff
rounds are read off the fixtures rather than assumed from the number, the same
rule the season page uses.

All three dates are built. The draft is `seasons.draft_at`, migration 060 --
an instant rather than a date, because people turn up to a draft at an hour,
entered in league time through the same pair the keeper windows use. It is
also the one that may be left blank: a season row is made when the schedule is
out and the league has settled its rules, while when everyone can get in a
room is a diary problem that comes later. Not derived from week one either,
though 2026's draft fell exactly on the Tuesday that opens it -- that is where
this league happened to put it once, not a rule, and a derivation has no way
to be corrected the year it is wrong.

2026 is recorded in full: drafted Tue 8 Sep, week one Sun 13 Sep, trading
closes Sat 28 Nov, which is week 12. 2022-25 have only their week ones; what
those seasons ran under is not written down anywhere, and a date inferred from
where the trades happen to stop would be a guess wearing the clothes of a
record.

Two things the page says that nobody recorded. The **playoff weeks** are shown
for a season whose bracket is not drawn -- the generator lays out fourteen
weeks and stops, so a calendar built from fixtures ended in mid-December --
marked "not drawn yet", because certain is not the same as drawn. And the
**assembly window** a season is expected to sit in, the Tuesday week fifteen
opens to the Tuesday week seventeen opens, on a hollow pill where every other
pill is filled: a date nobody has set is not a date. The two agree without
being made to, which is a small check on both.

The shape of a year is one table rather than a section each, and it stacks
below 48rem -- three columns of label, value and pill do not fit a phone, and
right-aligning them pushed the short answers off the edge entirely.

### Still to do

- **The trade-deadline check.** The date is stored and displayed; nothing yet
  compares it against what the transaction import loads. A trade dated after
  the deadline should be caught the way `/admin/rosters` catches a roster that
  disagrees with the record. Week 12 for 2026 sits comfortably later than any
  trade the league has made -- the latest of 63 sides falls in week 11 -- so
  the check would be quiet today, which is the right time to build it.

## 4. ~~Admin imports from text files~~ **Done, and the rest struck**

Four are built, all on the same pattern and all worth copying from:
`/admin/draft`, `/admin/transactions`, `/admin/rosters` and `/admin/adp`.
What that pattern settled --

- the parser is a module of its own, takes text, touches no database, and
  reports what it could not read rather than guessing;
- the preview is a separate press from the write, and carries the paste
  through in a hidden field so what is saved is what was read;
- a tickbox appears only for a thing the page has just told you about;
- Yahoo prints the same data more than one way, and a loader that knows only
  the shape it was written against will meet the other one.

**The other five are struck, not deferred.** The table was written when three
loaders existed and the rest looked like a set. They are not: every one has a
live writer in the app already, so a loader would be a second way to do
something there is already a way to do.

| Element | Table | What writes it now |
|---|---|---|
| Seasons | `seasons` | `/admin/season-setup`, step one |
| Teams | `teams` | `/admin/season-setup`, step two |
| Owners | `owners` | `/admin/owners`, Add an owner |
| Players | `players` | **a side effect** of `/admin/transactions` and `/admin/draft` |
| Matchups | `matchups` | `/admin/schedule` draws the fixtures, `/admin/scores` fills them |

Players is the whole argument in miniature: nobody has to load a player,
because the two loaders that name a player's position create him on the way
past. That is exactly why `/admin/rosters` says to load the transactions
first -- a roster names no positions and so cannot.

What is genuinely missing is a different shape, and only worth building if it
is ever wanted: **there is no way to take in a whole season's results from
outside.** The schedule page draws fixtures, it does not ingest somebody
else's, and entering a year by hand is seventeen weeks of score entry. That
matters only for a season before 2022 or from another league.

`keeper_selections` was the sixth row of this table and has moved to item 17.
It was never an import problem.

**Items 5 to 12 are the UI audits.** The same treatment `/history`, `/season` and `/team` were given: read the
page, say what is wrong with it, agree the changes, build them. **Each is its
own item** — one audit, one conversation. **All twelve are done.**

One thing they all taught, worth carrying into anything new: **measure at
more than one width.** `/admin/keepers/edit` was 412px wide at 390, 375 and
360 and exactly 420 at 420, which is the width these were habitually probed
at, so a single measurement had cleared it.

A design and usability audit rather than form validation. `/rules` was on
the list and has no inputs at all, which settled which is meant.

Every one looked at this way was worth a handful of real changes, and
several were worth a data-losing bug.

## 5. ~~Audit `/admin/scores`~~ **Done**

Ten findings, the first two of which lost data. The page is the one admin
page used every week of a season, and it earned the most polish because of
it.

Picking the wrong Round deleted the week. A save clears the week and rewrites
it, and Final and Semifinals skipped the every-team-entered check that
Regular season got -- so their rows came back empty, the empty rows were
skipped, and nothing was inserted after the delete had already run. Two
clicks emptied a played week and the page said "Saved 0 matchups" in green.

`WRITEABLE` and `SUMMARY_KINDS` were two lists of the same three things four
hundred lines apart with nothing forcing them to agree. This project has been
bitten by that shape before -- the round-collision rule in six copies -- so
the check reads the one list that has to exist anyway.

The rest was shape: the week loads when you pick it (`loadonpick`, the
summaries picker's opt-in, with the Load button kept in the markup for a
scriptless browser), the two dropdowns stopped sitting against the far left
of a wide panel, a failed recap said so on the page rather than only in the
log, and the write buttons moved into an action row with the note above it
rather than a bare span trailing after.

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

## 10. ~~Audit `/admin/rivals`~~ **Done**

Six findings. Generate and save deletes every rivalry the season has and
writes six new ones -- and the Override below it exists so a commissioner can
decide something the weighting cannot, which generating threw away without a
word. Both acts ask first now, and a hand-made pairing is counted and named
in the question.

Worth recording, because it was asserted before it was checked: this does
*not* rewrite which games the history pages call rivalry meetings. Scores
read `season_year <= season`, so a finished year's own games count toward its
own scoring -- but generating today reproduces the stored pairings exactly
for all five seasons. The manual override was the thing at risk.

This was the last page choosing its season from a dropdown and a Load button.
The override's twelve dropdowns also sat some nine hundred pixels from the
managers they pair, on the sheet's right-aligned default.

## 11. ~~Audit `/admin/schedule`~~ **Done**

Six findings. Destructive on save and hard to eyeball was the right thing to
have flagged: two of the six were the destruction.

Rolling a schedule onto a season with scores in it corrupted that season. The
save deleted only the unscored matchups and inserted a fresh fourteen weeks
with `on conflict do nothing` -- and a new pairing in a played week does not
collide with the played one, so it landed beside it. Against 2025 that turns
84 matchups into 159, nine to twelve games in every week, managers playing
twice. A tickbox was the only thing in the way.

The save did not save what was previewed. It regenerated instead, and the two
reads disagreed: the page ordered owners by username and the save had no
`order by` at all. `generate_schedule`'s output depends on that order, so the
preview and the save agreed by luck. Sorting the ids inside the generator
makes the seed the only input that matters -- eight shuffles now give one
schedule.

And the page could only ever draw a schedule it had just rolled, so once one
was saved the only way to see it was to roll another and hope it matched. It
reads the saved matchups now, and a rolled one is labelled "Rolled, not
saved" with a link back to the saved one, so which of the two is on screen is
never a guess.

## 12. ~~Audit `/admin/crests`~~ **Done**

Item 14 had rebuilt this page as Assembly admin to a brief; the fresh-eyes
pass found five things, one of them a flow that could not complete.

`poll_state` calls an assembly risen the moment its closing time passes.
Proclaim asked `closed_at`, which only Rise it now sets, and that button is
drawn only while a poll sits -- so an assembly left to run out showed its
count, offered Proclaim and refused it, with nothing left on the page to
unstick it. The proclamations page listed seasons on `closed_at` too, so the
count it told the league to go and read was on no page. Both ask `poll_state`
now, which is the thing its own docstring says it is for.

Also: taking an honour back had no confirmation, which is the control that
loses league history; it posted the page's season rather than the grant's;
and `owners` was still being queried for a dropdown removed in item 14.

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

## 15. ~~The last week's account is written and shown nowhere~~ **Done**

Settled the other way from how this item framed it. It read as a display
problem -- find somewhere to put week seventeen -- and the answer is that
there is nothing to put. The two 2025 texts were compared side by side: the
season recap carries the final, the third-place game, seventh and ninth,
near enough line for line, at 3060 characters against 885. What only the
weekly one had was four week-scoped superlatives -- highest and lowest of
the week, closest game, margin against the reckoning. The finals are the
story of the year rather than of the week.

So the last week of a season no longer gets an account at all, which is
cheaper than displaying one: no call, no retry ladder when that call fails,
and no draft sitting in the admin badge clearable only by publishing
something invisible. The week is read off the championship game rather than
by taking the highest week with fixtures -- mid-season, before the bracket is
drawn, the highest week with fixtures is the fourteenth, and skipping that
would lose an account somebody wants.

2025's week seventeen is kept. It is published, it costs nothing where it
sits, and the summaries page already said "Published, but on no page". Both
pages refuse to write another and say why, and the summaries page blocks
rather than dropping the week from its picker -- a week off the list is a
week nobody can reach to read or discard.

### What this item said before



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


## 17. ~~Nothing in the app wrote the keeper record~~ **Done**

Moved here from item 4's table of loaders, where it was the sixth row and did
not belong: it was never an import problem. Two tables, neither of which the
app had ever written.

`keeper_contracts` had only ever been filled by two hand-run import scripts,
so the 2026 selection produced six signings existing only as a `term_years`
on a submission. Three were three-year deals. Come 2027, eligibility reads
contracts for `state='contract'`, finds none, and six obligations show up as
free choices.

A contract is created when the commissioner approves the submission that
signs it -- the moment the terms stop being able to move. Not at resolution,
because a plan becomes a pending submission that can still be edited or
rejected. The ADP round is on the review row and what is on screen at
approval is what the contract keeps, so a later list cannot reprice a deal
already signed.

`keeper_selections` is what the league kept, and eleven things read it:
eligibility, the four-season maximum, the draft board, Three Oaths Sworn.
2026 resolved three keeper rounds and left it empty. It is **derived** now
rather than kept in step -- a submission can be approved, edited, rejected or
deleted from four routes, and a parallel record would have to be corrected in
all four. Miss one and the two drift, which is the silence this table already
failed in. Rebuilding from the approved submissions is idempotent, so running
it after anything is always right, and it is called from all five places that
change one.

2026 was backfilled through the same two functions the routes use, by a
script dry until told otherwise: six contracts and thirty selections,
matching what was agreed beforehand row for row. What it changes for 2027 --
six obligations that would have read as free choices, three repriced by the
rule (Drake Maye thirteenth round to third, Jaxon Smith-Njigba ninth to
fourth, Brock Bowers thirteenth to sixth), and thirteen players kept four
times who cannot be kept again.

**A note for whoever adds the next thing that changes a submission.** There
are five call sites for the rebuild and nothing forces a sixth to exist. If
you add a route that approves, edits, rejects or deletes one, it has to call
`sync_keeper_selections` -- the table is derived, not maintained, and a route
that forgets leaves no error behind, only a wrong archive that reads as
right.

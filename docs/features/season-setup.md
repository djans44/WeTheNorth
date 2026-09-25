# Feature: Create-season checklist

**Built, and rehearsed against a Neon branch.** `/admin/season-setup` walks
through everything needed to stand up the next league year, in dependency
order, showing what is done and what is standing in the way of what is not.

Steps 1 and 2 are forms on that page; the rest link out to the pages and
scripts that already do the work. What used to be folklore -- the order, and
which step is waiting on which -- the page now states.

## Rehearsing it

`scripts/reset_season.py` tears a year down, dry by default.
`scripts/serve_rehearsal.py` runs the app against a Neon branch. Both go
through `scripts/_rehearsal.py`, which refuses to do anything if the branch
resolves to the same host as `.env`.

2026 was torn down and rebuilt end to end on a branch: both forms, the ADP
import, rivalries, schedule, lottery, twelve slot picks, the keeper windows,
and resolving round 1, which wrote twelve submissions. The checklist tracked
every step, including holding the keeper round at waiting until all twelve
slots were chosen.

## The steps, in the order the code forces

Dependencies are real, not stylistic — each of the generators reads the output
of the one before it.

| # | Step | Today |
|---|---|---|
| 1 | `seasons` row: year, team and keeper counts, **week one, the trade deadline, draft day** | a form on `/admin/season-setup`, counts cloned from last year and the dates this year’s |
| 2 | `teams` rows, one per owner for the season | a form on `/admin/season-setup`, carried forward from last season |
| 3 | Import ADP for the season | `/admin/adp` — **needed before keeper rounds resolve, not just before the draft** |
| 4 | Rivalries | `/admin/rivals` |
| 5 | Schedule | `/admin/schedule` — needs teams **and** rivalries, since week 10 is rivalry week |
| 6 | Draft order lottery, then slot selection | `/admin/draft-order`, `/draft-order` |
| 7 | Keeper windows, then resolve each phase | `/admin/keepers` |
| 8 | Import draft results once the draft happens | `/admin/draft` |

Steps 4, 5 and 6 all take their entrants from `teams` rows for that season, so
step 2 is the keystone: nothing downstream can run without it.

Step 6 comes before step 7 deliberately — slot choice happens before keeper
selection, so an owner knows their pick position while deciding who to keep.

**Step 3 can be silently partial, which is worse than missing.** The parser
matches on normalised name and skips anyone with no `players` row, reporting
the skips and carrying on. So ADP imported before the players exist is not
absent -- it is short, by however many the league had not seen yet. The page
says how many it matched and names what it skipped, which is the half the
script only wrote to the terminal.

That is not hypothetical. 2026's ADP was imported with 197 of the file's
players matched. Re-running the same file two weeks later matched 212: fifteen
players had been added in between by the roster and transaction imports. None
of the fifteen was keeper-eligible for 2026, so nothing was mispriced, and
production has since been topped up to 212. In a year where one of them *is*
on the previous season's roster, the fallback below applies to a player nobody
knew was missing.

Re-running it is safe and is the fix: it is an upsert on
`(season_year, player_id)` with no deletes, so it tops up what is short and
rewrites the rest with the same values. It does not retro-price existing
contracts either, because a signed contract stores its own `contract_round`.

Step 3 is also easy to leave until draft day and get quietly wrong.
`keeper_eligibility` left-joins `player_adp_rounds` on the upcoming season and
falls back to `coalesce(a.contract_cost_round, 13)`. With no ADP imported, every
3-year contract's later-years price silently becomes round 13 instead of
`LEAST(original round, ADP round + 2)`. Nothing errors, nothing looks empty, and
the price is locked at signing — so it is wrong for three years. The checklist
should treat missing ADP as blocking for step 7, not advisory.

Step 5 already refuses to run without step 2 and step 4: `/admin/schedule`
rejects an odd entrant count and rejects incomplete rivalries by name.

## The gap this closed

**Adding an owner to a season had no UI.** `/admin/owners` creates the person
and deliberately stops there, because the rivalry and schedule generators both
need an even number of entrants and writing a `teams` row from that page would
quietly break them the next time either ran. The retire-one/add-one flow ended
with a `teams` row inserted by hand.

It is step 2 on this page now, where the team-count invariant is visible and
enforced against `seasons.team_count` rather than guessed at from elsewhere,
and where each owner's team name for the new year is set at the same time.

Removing an owner is deliberately not a delete. A `team_id` is referenced by
matchups, rosters, draft picks, keeper selections and transactions, so an owner
who already has results against their name that season is kept and reported by
name rather than cascaded away.

## Open questions

Struck through where settled, with what was decided and why. The rest are
still open and are written down so the decision gets made once rather than
rediscovered halfway through building something.

**Where the season comes from**

1. ~~Does the page create the `seasons` row itself?~~ **Settled: it does.**
   A season row you cannot create is the reason step 1 was folklore.
   `yahoo_league_key` is left alone for now; nothing reads it.
2. ~~Blank or cloned?~~ **Settled: cloned** from the most recent earlier
   season. An odd `team_count` is refused outright, because the rivalry and
   schedule generators both pair the league up.
3. If `seasons.team_count` and the actual number of `teams` rows disagree,
   which one wins? The league-size cap on `/admin/owners` already trusts
   `team_count`.

**Filling the roster (step 2)**

4. ~~Carry last season's owners forward?~~ **Settled: yes**, ticked, with
   their team names prefilled. Retired managers are listed but never ticked,
   so adding one back is a tick rather than a hunt. Note this carries the
   *previous* year's team names: three of the twelve had changed for 2026, so
   the names are a starting point to edit, not an answer.
5. Does the checklist drive retiring a departing owner, or does that stay on
   `/admin/owners` with the checklist only reporting the count? The
   retire-one/add-one flow currently spans both.
6. ~~Team names for the new year?~~ **Settled with 4:** carried forward from
   last season and editable in the same form. `/admin/owners` can still change
   one afterwards.

**How strict the page is**

7. ~~Block, or show not-ready and allow?~~ **Settled: show and allow.**
   `/admin/schedule` already hard-refuses without teams and rivalries, and a
   softer gate in front of a hard one only moves the message.
8. ~~Link out or embed?~~ **Settled: link out**, except steps 1 and 2 which
   have nowhere to link to. Embedding would mean a second implementation of
   the rivalry, schedule and keeper generators.

**Re-running steps**

9. Regenerating rivalries after the schedule is saved leaves week 10 wrong —
   the schedule bakes the pairings into `matchups` at generation time and
   never looks at `rivalries` again. Warn, block, or offer to regenerate the
   schedule too?
10. ~~Re-importing ADP after a 3-year contract has been signed?~~
    **Settled: safe, and checked rather than assumed.** A signed contract
    stores its own `contract_round`, so a later import does not reach it.
    Topping 2026 up from 197 rows to 212 moved no keeper price: all 172
    eligibility rows were compared before and after and none changed.

    Re-running the lottery was the other half of this question and is now
    settled: `/admin/draft-order/lottery` refuses to draw over an order that
    already exists. Removing it is its own button and asks first, so drawing
    silently over twelve lottery positions is not reachable.

**Scripts that are not UI**

11. Steps 3 and 8 are command-line scripts. Does the checklist just instruct
    and link, or does it need file upload? Note Render's filesystem is
    ephemeral, so an upload has to be parsed in the request and written to
    Postgres, never staged on disk.
12. Step 8 happens after the draft, not during setup. Does it belong on this
    page at all, or is the checklist finished once the draft board is ready?

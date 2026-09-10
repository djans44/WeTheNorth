# Feature: Create-season checklist

**Not designed yet.** This is a placeholder so the shape of the problem is
written down while it is fresh, not a spec to build from.

An admin page that walks through everything needed to stand up the next league
year, in dependency order, showing what is done and what is outstanding. Today
those steps are scattered across four admin pages, two migrations and a handful
of scripts, and knowing the right order is folklore.

## The steps, in the order the code forces

Dependencies are real, not stylistic — each of the generators reads the output
of the one before it.

| # | Step | Today |
|---|---|---|
| 1 | `seasons` row: `season_year`, `team_count`, `keeper_count`, `yahoo_league_key` | **no UI** — only ever created by migration `005` |
| 2 | `teams` rows, one per owner for the season | **no UI** — only ever created by migration `009` |
| 3 | Import ADP for the season | `scripts/import_adp_text.py` — **needed before keeper phases resolve, not just before the draft** |
| 4 | Rivalries | `/admin/rivals` |
| 5 | Schedule | `/admin/schedule` — needs teams **and** rivalries, since week 10 is rivalry week |
| 6 | Draft order lottery, then slot selection | `/admin/draft-order`, `/draft-order` |
| 7 | Keeper windows, then resolve each phase | `/admin/keepers` |
| 8 | Import draft results once the draft happens | `scripts/import_draft.py` |

Steps 4, 5 and 6 all take their entrants from `teams` rows for that season, so
step 2 is the keystone: nothing downstream can run without it.

Step 6 comes before step 7 deliberately — slot choice happens before keeper
selection, so an owner knows their pick position while deciding who to keep.

Step 3 is easy to leave until draft day and get quietly wrong.
`keeper_eligibility` left-joins `player_adp_rounds` on the upcoming season and
falls back to `coalesce(a.contract_cost_round, 13)`. With no ADP imported, every
3-year contract's later-years price silently becomes round 13 instead of
`LEAST(original round, ADP round + 2)`. Nothing errors, nothing looks empty, and
the price is locked at signing — so it is wrong for three years. The checklist
should treat missing ADP as blocking for step 7, not advisory.

Step 5 already refuses to run without step 2 and step 4: `/admin/schedule`
rejects an odd entrant count and rejects incomplete rivalries by name.

## The gap this has to close

**Adding an owner to a season has no UI.** `/admin/owners` creates the person
and deliberately stops there, because the rivalry and schedule generators both
need an even number of entrants and writing a `teams` row from that page would
quietly break them the next time either ran. The result is that the
retire-one/add-one flow currently ends with a `teams` row that has to be
inserted by hand.

**Do that here.** Assigning owners to a season belongs in this checklist at
step 2, where the team-count invariant is visible and can be enforced against
`seasons.team_count` rather than guessed at from another page. That is also the
natural place to set each owner's team name for the new year, which
`/admin/owners` can already edit once the row exists.

## Open questions

None of these are decided. They are written down here so the decisions get made
once, when the page is designed, rather than being rediscovered halfway through
building it.

**Where the season comes from**

1. Does the page create the `seasons` row itself — year, `team_count`,
   `keeper_count`, `yahoo_league_key` — or does that stay a migration?
2. Is a new year created blank, or cloned from the previous one? `team_count`
   has been 12 and `keeper_count` 3 for every season so far, so cloning is
   almost always right.
3. If `seasons.team_count` and the actual number of `teams` rows disagree,
   which one wins? The league-size cap on `/admin/owners` already trusts
   `team_count`.

**Filling the roster (step 2)**

4. Does step 2 default to carrying last season's owners forward? In practice
   the roster changes by at most a manager or two a year, so starting from
   last year's twelve and editing is probably less work than starting empty.
5. Does the checklist drive retiring a departing owner, or does that stay on
   `/admin/owners` with the checklist only reporting the count? The
   retire-one/add-one flow currently spans both.
6. Team names for the new year: set here while assigning owners, carried
   forward from last season, or left to `/admin/owners` afterwards? Carrying
   forward matches what the data does — several managers keep a name for years.

**How strict the page is**

7. Does the checklist *block* a step whose prerequisites are unmet, or only
   show it as not-ready and let an admin proceed? The house style cuts both
   ways: `/admin/schedule` already hard-refuses without teams and rivalries,
   but admin keeper overrides deliberately allow a duplicate round. Pick one
   and be consistent.
8. Is it a checklist that *links out* to the existing admin pages, or one that
   *embeds* each step? Linking out is far less code and keeps one
   implementation of each step; embedding gives the single-sitting flow the
   feature is really for.

**Re-running steps**

9. Regenerating rivalries after the schedule is saved leaves week 10 wrong —
   the schedule bakes the pairings into `matchups` at generation time and
   never looks at `rivalries` again. Warn, block, or offer to regenerate the
   schedule too?
10. Re-importing ADP after a 3-year contract has been signed. ADP is
    **locked at signing** by design, so a late import must not retro-price
    existing contracts.

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

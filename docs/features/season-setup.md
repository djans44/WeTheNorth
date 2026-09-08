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
| 3 | Import ADP for the season | `scripts/import_adp_text.py` |
| 4 | Rivalries | `/admin/rivals` |
| 5 | Schedule | `/admin/schedule` — needs teams **and** rivalries, since week 10 is rivalry week |
| 6 | Draft order lottery, then slot selection | `/admin/draft-order`, `/draft-order` |
| 7 | Keeper windows, then resolve each phase | `/admin/keepers` |
| 8 | Import draft results once the draft happens | `scripts/import_draft.py` |

Steps 4, 5 and 6 all take their entrants from `teams` rows for that season, so
step 2 is the keystone: nothing downstream can run without it.

Step 6 comes before step 7 deliberately — slot choice happens before keeper
selection, so an owner knows their pick position while deciding who to keep.

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

Worth deciding at the same time: whether step 2 defaults to carrying last
season's owners forward, since in practice the roster changes by at most one or
two managers a year.

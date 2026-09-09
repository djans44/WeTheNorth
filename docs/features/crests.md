# Feature: Crests of honour

Honours owners earn across seasons, displayed on manager pages and in a
league-wide case.

**They are called crests of honour everywhere the league sees them**, and the
code says `crests` too. A page that says one thing while the table says another
is how a codebase starts needing a glossary. The tables are `crests` and
`owner_crests`.

## Two kinds, and the difference matters

| | **Earned** | **Held** |
|---|---|---|
| Who has it | Anyone who has ever done the thing | Exactly one manager at a time |
| Example | Five straight, Champion 2025 | Reigning champion, Fiercest rival |
| Lifetime | Kept forever | Passes to whoever takes it next |
| Rows | One per owner per season, accumulating | One row, replaced on recompute |
| Shown on | Manager page | Manager page, `/history`, and the avatar ring |

An earned crest is a fact about the past and never moves. "Champion, 2025" is
David's whether or not he wins again. A held crest is a fact about the present:
the reigning champion is David only until someone else lifts it.

The same underlying fact often produces both. Winning 2025 earns David the
`champion` crest for that season, permanently, and puts the `reigning_champion`
crest in his hands until 2026 is decided. That is deliberate, not duplication:
one is a record, the other is a title.

### Held crests

| Code | Name | Held by |
|---|---|---|
| `reigning_champion` | The reigning champion | Winner of the most recent completed championship |
| `reigning_sacko` | Holder of the Sacko | Whoever finished twelfth most recently |
| `fiercest_rival` | Fiercest rival | The best win rate against their own rival, minimum four meetings |

More can be added; these three are enough to prove the mechanism.

**Each crest carries its own tiebreak**, written into its rule rather than left
to a policy at the top of the file -- what separates two champions is not what
separates two rivals. Where a rule runs out of tiebreaks the crest is shared, so
the display must not assume exactly one holder, and the avatar mark goes to
every holder rather than to one of them picked arbitrarily.

| Code | Tiebreak |
|---|---|
| `reigning_champion` | None needed; one championship game, one winner |
| `reigning_sacko` | None needed; one twelfth place |
| `fiercest_rival` | Meetings played, then shared |

### The avatar ring and mark

A held crest marks its holder's sigil wherever that sigil appears, so the
reigning champion is recognisable in the standings without a legend. Two parts:

- **A ring** in the crest's colour -- gold for the champion, oxblood for the
  Sacko, bronze for the fiercest rival.
- **A small mark overlaid** on the sigil, bottom-right, in the same colour on
  the page ground so it reads as a seal rather than a smudge on the sigil.

Rules, because the sigil is the site's most-repeated element and cannot be
allowed to get noisy:

- **One ring, one mark.** A manager holding two crests wears the one with the
  lowest `sort_order`; their page lists both.
- **The ring replaces the sigil's existing hairline**, not added on top of it,
  so an unringed sigil is unchanged and the two sit at the same size.
- **The mark needs room.** Sigils are drawn from 20px in bracket ties to 96px on
  a manager page. Overlaid on a 20px sigil the mark is about 8px, which the
  seed-mark work already proved is a smudge rather than a shape. Below 28px the
  ring shows alone -- the ring is what carries "this manager holds something",
  and the mark is what says which. Anything smaller keeps the first meaning and
  drops the second.
- **Nothing else changes.** No crest count, no second glyph, no tooltip in a
  table. The manager page is where crests are explained.

## Decisions, confirmed

- **Both tiers exist**, but only the auto tier is built now. The schema carries
  `award_mode` and `awarded_by` from the start so the commissioner grant flow
  needs no migration when it comes.
- **Backfill where the data is real, award nothing where it is blind.** The
  history is not evenly loaded, and the gaps are not "no activity" — they are
  "no records". See the table below.
- **Rivalry crests are rebuilt**, because the ones originally specced cannot be
  awarded honestly. See Rivalry.

## What the data can actually support

Checked against the database rather than assumed:

| Source | Rows | Seasons | Consequence |
|---|---|---|---|
| `matchups` | 492 | 2022–2026 | Silverware and scoring backfill in full |
| projections on `matchups` | 408 | 2022–2025 | Projection crests backfill in full |
| `teams` | 60 | 2022–2026 | Tenure backfills in full |
| `keeper_selections` | 104 | **2023–2025** | Keeper crests backfill three seasons, not four |
| `transactions` | 301 | **2025 only** | Transaction crests are 2025 and later |
| `keeper_voids` | 2 | **2026 only** | `cut_bait` backfills empty |
| `rivalries` | 60 | 2022–2026, but retroactive | See Rivalry |

**The trap in that table is `set_and_forget`** — "zero waiver adds all season".
For 2022 through 2024 the transactions table is empty because nothing was ever
imported, not because twelve managers all sat on their hands. Computing it over
those seasons would award it to everyone. A season with no transaction rows at
all must be skipped, not read as zero.

`four_year_man` is in the catalogue but is currently unearnable: there have been
three keeper seasons and the longest any player has been held is three years.
It becomes reachable in 2026.

## Schema

The primary keys here are `owners.owner_id` and `seasons.season_year`, not `id`
and `year` — an earlier draft of this file had those wrong and the migration
would not have run.

```sql
create table crests (
  id          serial primary key,
  code        text not null unique,
  name        text not null,
  description text not null,
  category    text not null,   -- silverware | scoring | keepers | transactions | rivalry | tenure | honours
  scope       text not null,   -- season | career
  standing    text not null,   -- earned | held
  award_mode  text not null,   -- auto | manual
  icon        text,
  sort_order  int not null default 0,
  active      boolean not null default true
);

create table owner_crests (
  id          serial primary key,
  owner_id    int not null references owners(owner_id) on delete cascade,
  crest_id    int not null references crests(id) on delete cascade,
  season_year smallint references seasons(season_year),
  detail      text,            -- "182.4 pts, week 6"
  awarded_at  timestamptz not null default now(),
  awarded_by  int references owners(owner_id)  -- null for auto-computed
);
```

`detail` is what makes a crest worth hovering over. Populate it on every auto
crest with the number that earned it.

### Uniqueness gotcha

Postgres treats NULLs as distinct, so a plain `unique (owner_id, crest_id,
season_year)` will happily allow duplicate career crests. Use two partial indexes:

```sql
create unique index owner_crests_season_uniq
  on owner_crests (owner_id, crest_id, season_year)
  where season_year is not null;

create unique index owner_crests_career_uniq
  on owner_crests (owner_id, crest_id)
  where season_year is null;
```

## Catalogue

### Silverware — from `final_standings`

| Code | Name | Earned by |
|---|---|---|
| `champion` | Champion | Winning the championship game |
| `runner_up` | Runner-up | Losing the championship game |
| `bronze` | Third place | Winning the third-place game |
| `regular_season_crown` | Best in the North | Best regular season record |
| `sacko` | The Sacko | Finishing 12th |
| `dynasty` | Dynasty | Back-to-back championships (career) |

The held counterparts of the first and fifth rows -- `reigning_champion` and
`reigning_sacko` -- are listed under Held crests, not here. A season crest
records who won; a held crest records who currently is.

### Scoring — from `matchups`

| Code | Name | Earned by |
|---|---|---|
| `weekly_high` | Highest score | Top single-week score in a season |
| `season_high_points` | Points leader | Most total regular season points |
| `points_against_king` | Cursed | Most points against in a season |
| `blowout` | Massacre | Largest margin of victory in a season |
| `nailbiter` | By a whisker | Winning by under a point |
| `streak_five` | Five straight | Five consecutive regular season wins |
| `gauntlet` | Gauntlet | Beating every opponent faced in a season |

### Projections — from the projection columns

| Code | Name | Earned by |
|---|---|---|
| `overachiever` | Defied the odds | Beating projection by the widest margin that season |
| `robbed` | Robbed | Losing while projected to win by 20+ |

### Keepers and contracts

| Code | Name | Earned by |
|---|---|---|
| `four_year_man` | Four-year man | Holding one player all four keeper seasons |
| `cut_bait` | Cut bait | Voiding a contract |
| `triple_threat` | Fully committed | Holding three contracts at once |
| `forfeit` | Asleep at the wheel | Missing a keeper window and forfeiting a slot |

### Transactions — from `transactions`

| Code | Name | Earned by |
|---|---|---|
| `waiver_warrior` | Waiver warrior | Most adds in a season |
| `set_and_forget` | Set and forget | Zero waiver adds all season |
| `wheeler_dealer` | Wheeler-dealer | Most trades in a season |

### Rivalry

The two crests originally specced here — beating your rival on rivalry week, and
sweeping them — cannot be awarded honestly. The `rivalries` table does hold
pairings for 2022–2025, but they were generated retroactively from past meetings
and nobody played a grudge match at the time. Rivalry week itself only exists
from 2026: across all five seasons exactly one week has every game a rival
meeting, and it is 2026's tenth.

| Code | Name | Scope | Earned by |
|---|---|---|---|
| `fiercest_rival` | Fiercest rival | career | The best win rate against your own rival, across every meeting ever |
| `rivalry_week_high` | Lord of the grudge | season | Highest score of anyone in rivalry week |

`fiercest_rival` is honest backfill: it counts real head-to-head results, and
makes no claim that the fixture meant anything at the time.

**Minimum four meetings**, confirmed. On win rate alone it would go to Niall at
2-0 against Curtis, from two meetings, over Joey at 5-2 against Tulio from
seven. Four is roughly one a season for a pairing that has existed throughout,
and it gives the crest to Joey. Ties on rate are broken by meetings played, then
shared.

`rivalry_week_high` will sit unawarded until 2026 week 10 is played. That is
expected, not a bug — an unearned crest still shows dimmed in the case, which is
the point of showing what is available to chase.

### Tenure

| Code | Name | Earned by |
|---|---|---|
| `founding_member` | Founding member | Played in 2022 |
| `veteran` | Veteran | Five seasons played |

### Honours — manual only, deferred

Not built in this pass. The catalogue rows and the `award_mode` column exist so
that adding the grant flow later is a route and a template, not a migration.


Commissioner-granted, no computation. Seed a handful and let admins add more:
best team name, draft-day antics, trade of the year, worst beat. These are the
ones people actually talk about, so make the admin grant flow quick — pick owner,
pick crest, optional season, optional detail line, save.

## Recompute

Auto crests are derived, so treat them as a rebuild rather than an append:

1. Delete all `owner_crests` rows where the crest's `award_mode = 'auto'`, scoped
   to the season being recomputed.
2. Recompute and insert.
3. Never touch rows where `award_mode = 'manual'` or `awarded_by is not null`.

Held crests are rebuilt whole rather than per season: there is one holder, and
the question "who holds it now" has no season to scope to. Delete every row for
a held crest and insert the current holder or holders.

Expose it at `/admin/crests` with a per-season and an all-seasons button, and call
it automatically after weekly score entry so standings-derived crests stay current
mid-season. Show a preview of what would change before committing, matching the
preview-then-confirm pattern already used for keeper phase resolution.

Ties: award to everyone tied rather than picking arbitrarily. Only award
season-scoped scoring crests for seasons that have completed results, so an
in-progress 2026 doesn't hand out a points leader in week 3.

## Display

- **Manager page**: the case, carrying both kinds. Held crests first and set
  apart -- they are titles, and a manager who holds one should see it before a
  list of things they once did. Then the earned crests grouped by category, in
  full colour where won and dimmed where not, so people can see what is
  available to chase. Hover or tap shows the description and the `detail` line.
- **`/history`**: the held crests and who currently holds each. This is the only
  place the whole league's standing is visible at once, and it is a short
  section -- three crests, three sigils, three names.
- **The avatar ring**: everywhere a sigil appears, as above.
- **Inline**: the two or three rarest earned crests beside the sigil in the
  manager page header only. Not in standings tables -- that is what the ring is
  for, and the two together would be clutter.

Rarity is a count of holders, computed on the fly. No stored tier column.

## Naming

`crests` and `owner_crests` in the schema, "crests of honour" in the copy, and
"crest" in the code. The word "badge" should not appear in anything the league
reads. The URL is `/crests`.

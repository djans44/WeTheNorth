# Feature: League badges

Awards owners earn across seasons, displayed on manager pages and in a league-wide
trophy case.

> **Two decisions were assumed rather than confirmed.** Both tiers exist
> (auto-computed plus commissioner-granted), and auto badges are backfilled across
> 2022–2025. If either is wrong, change it here before building — the backfill
> assumption in particular shapes the recompute design.

Backfilling is worth it: all four completed seasons of matchups, drafts, rosters,
transactions and contracts are already loaded, so the history is free. A badge
system that starts empty in 2026 has nothing to look at on launch day.

## Schema

```sql
create table badges (
  id          serial primary key,
  code        text not null unique,
  name        text not null,
  description text not null,
  category    text not null,   -- silverware | scoring | keepers | transactions | rivalry | tenure | honours
  scope       text not null,   -- season | career
  award_mode  text not null,   -- auto | manual
  icon        text,
  sort_order  int not null default 0,
  active      boolean not null default true
);

create table owner_badges (
  id          serial primary key,
  owner_id    int not null references owners(id) on delete cascade,
  badge_id    int not null references badges(id) on delete cascade,
  season_year int references seasons(year),
  detail      text,            -- "182.4 pts, week 6"
  awarded_at  timestamptz not null default now(),
  awarded_by  int references owners(id)   -- null for auto-computed
);
```

`detail` is what makes the badge worth hovering over. Populate it on every auto
badge with the number that earned it.

### Uniqueness gotcha

Postgres treats NULLs as distinct, so a plain `unique (owner_id, badge_id,
season_year)` will happily allow duplicate career badges. Use two partial indexes:

```sql
create unique index owner_badges_season_uniq
  on owner_badges (owner_id, badge_id, season_year)
  where season_year is not null;

create unique index owner_badges_career_uniq
  on owner_badges (owner_id, badge_id)
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

| Code | Name | Earned by |
|---|---|---|
| `rivalry_week_win` | Won the grudge match | Beating your rival on rivalry week |
| `rival_sweep` | Swept the rival | Beating your rival both times you played |

Rivalries only exist from the season they were introduced, so these backfill as
empty for earlier years. That's fine — don't fabricate historical rivals.

### Tenure

| Code | Name | Earned by |
|---|---|---|
| `founding_member` | Founding member | Played in 2022 |
| `veteran` | Veteran | Five seasons played |

### Honours — manual only

Commissioner-granted, no computation. Seed a handful and let admins add more:
best team name, draft-day antics, trade of the year, worst beat. These are the
ones people actually talk about, so make the admin grant flow quick — pick owner,
pick badge, optional season, optional detail line, save.

## Recompute

Auto badges are derived, so treat them as a rebuild rather than an append:

1. Delete all `owner_badges` rows where the badge's `award_mode = 'auto'`, scoped
   to the season being recomputed.
2. Recompute and insert.
3. Never touch rows where `award_mode = 'manual'` or `awarded_by is not null`.

Expose it at `/admin/badges` with a per-season and an all-seasons button, and call
it automatically after weekly score entry so standings-derived badges stay current
mid-season. Show a preview of what would change before committing, matching the
preview-then-confirm pattern already used for keeper phase resolution.

Ties: award to everyone tied rather than picking arbitrarily. Only award
season-scoped scoring badges for seasons that have completed results, so an
in-progress 2026 doesn't hand out a points leader in week 3.

## Display

- **Manager page**: a case grouped by category, earned badges in full colour,
  unearned ones dimmed so people can see what's available to chase. Hover or tap
  shows the description and the `detail` line.
- **League trophy case** at `/badges`: the full catalogue with a count and owner
  avatars beside each badge.
- **Inline**: the two or three rarest badges an owner holds, shown small beside
  their avatar on their manager page header only. Don't scatter them into
  standings tables — that's where the avatars do the work.

Rarity is just a count of holders, computed on the fly. No stored tier column.

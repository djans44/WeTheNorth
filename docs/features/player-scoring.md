# Feature: Per-player scoring — not built, and what it would take

**There is no per-player scoring in this database.** Every points column
anywhere is a team total. Nothing records that Bijan Robinson scored 27.4 in
week 6, or which of a manager's starters won them the week.

This note exists so the next person to want it does not have to rediscover
that. It was found while planning the season page, when "name who won them
the week" turned out to be unanswerable.

## What the database actually holds

Every column with points in its name, and what it belongs to:

| Table or view | Columns | Belongs to |
|---|---|---|
| `matchups` | `team_a_points`, `team_b_points`, `team_a_projected`, `team_b_projected` | a **matchup** |
| `team_game_results` | `points_for`, `points_against` | a **team** in one game |
| `game_log` | `points_for`, `points_against` | the same, joined to owners |
| `team_season_stats` | `points_for`, `points_against` | a **team** in one season |
| `owner_all_time_stats` | `points_for`, `points_against`, `points_per_game` | an **owner**, all time |
| `owner_head_to_head` | `points_for`, `points_against` | an **owner** against one opponent |
| `owner_projection_stats` | `total_vs_projection`, `avg_vs_projection` | an **owner** |

Not one is keyed to a player.

The player-side tables carry no scores at all:

| Table | Columns | What is missing |
|---|---|---|
| `players` | `player_id`, `full_name`, `position` | everything |
| `rosters` | `season_year`, `team_id`, `player_id`, `acquired` | it is an **end-of-season snapshot**, not a weekly lineup |
| `draft_picks` | `season_year`, `round`, `pick_in_round`, `team_id`, `player_id`, `is_keeper` | who was picked, never how they did |
| `player_adp` | `adp` | a draft position, not points |
| `transactions` | `faab_amount` | money, not points |

`rivalries.score` is the pairing weight from the rivalry generator, and
`player_adp.adp` is an average draft position. Neither is a fantasy score.
They are worth naming because a grep for "score" finds them both.

## Why `rosters` is not enough

It is one row per player per team per **season**, written at the end of it.
It cannot say who was started in week 6, only who was on the roster when the
year finished. Two things follow:

- A player traded away in week 8 is on the *acquiring* team's snapshot and
  nowhere on the seller's, so a weekly view built from it would be wrong for
  every week before the trade.
- Started and benched are indistinguishable, so even with scores attached it
  could not answer "who won them the week".

The keeper rules lean on `rosters` heavily and correctly — a keeper is drawn
from the end-of-season roster, which is exactly what this table is. It is the
right table for its job and the wrong one for this.

## What it would take

1. **A Yahoo import of weekly player stats.** Yahoo has it; nothing here
   fetches it. `scripts/` has importers for drafts, rosters, transactions,
   matchups, ADP and keepers, so the shape is established — this would be one
   more, and by far the largest: 12 teams × 17 weeks × a full lineup, per
   season.

2. **A new table**, roughly `player_week_scores(season_year, week,
   player_id, team_id, points, started)`. `started` is the column that makes
   the whole thing worth having, and it is the one Yahoo makes hardest to
   backfill.

3. **A way to match players.** This is the real obstacle. `players.player_id`
   is a **local identity column**, not a Yahoo id — 1, 2, 3 — and every
   importer matches on `lower(full_name)` with a `(lower(full_name),
   position)` unique constraint. That works for a few hundred rostered
   players and will not survive weekly stats for every player in the NFL:
   suffixes, punctuation, name changes, two players sharing a name, and a
   defence that is a team rather than a person.

   Anything built here should probably add a `players.yahoo_id` first and
   backfill it, rather than pushing name matching further than it goes.

## What it would unlock

- Naming who won a manager their week, in the weekly summaries
- Crests about players rather than teams — a best single performance, a
  worst start, the bench that outscored the lineup
- Draft value: what a pick actually returned against what it cost
- Keeper value, which is the interesting one, since a keeper's price is a
  round and its return has never been measurable

None of it is blocked on anything but the import. The rest of the site is
team-level and stays that way; this would sit beside it.

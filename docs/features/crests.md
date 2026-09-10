# Feature: Crests of honour

What managers have won, and what they hold. Shown on a manager's page, on
each season, on `/history`, in the catalogue at `/crests`, and as a ring
around a sigil.

**They are called crests of honour everywhere the league sees them**, and the
code says `crests` too. A page that says one thing while the table says
another is how a codebase starts needing a glossary. The tables are `crests`
and `owner_crests`; the rules are `app/crests.py`.

## Two kinds, and the shape says which

| | **Crests** — earned | **Titles** — held |
|---|---|---|
| Who has it | Anyone who has ever done it | One manager at a time |
| Lifetime | Kept for good, and winnable again | Passes to whoever takes it |
| Drawn as | A shield, coloured by category | A roundel, coloured gold/silver/bronze |
| Also drawn as | — | A ring around the holder's sigil |
| Rows | One per owner per season, or per week | Three kinds — see below |

An earned crest is a fact about the past and never moves: "Crowned, 2025" is
David's whether or not he wins again. A title is a fact about the present.

The same event often produces both, which is deliberate. Winning 2025 earns
David `champion` for that season permanently and puts `reigning_champion` in
his hands until 2026 is decided. One is a record, the other is a title.

There are **28 earned crests and 9 titles**. What each is for is written on
`/crests`, one line each, straight from `crests.description` — that column is
the single source of the wording, and the site reads it rather than repeating
it.

## Titles

| Code | Name | Ring | Held by |
|---|---|---|---|
| `reigning_champion` | Protector of the Realm | gold | Won the most recent completed championship |
| `best_record` | The Ever-Victorious | silver | Best win rate in league history, minimum two seasons |
| `kingsguard` | Captain of the Kingsguard | silver | Longest winning streak still running, minimum two |
| `fiercest_rival` | Bane of Their Rival | silver | Best win rate against their own rival, minimum four meetings |
| `most_weekly_highs` | Master of Storms | silver | Has topped the league's scoring in more weeks than anyone |
| `most_narrow_wins` | The Fortunate | silver | Has won more games by under a point than anyone |
| `most_narrow_losses` | Hounded by Fate | bronze | Has lost more games by under a point than anyone |
| `court_fool` | The Court Fool | bronze | Longest losing streak still running, minimum three |
| `reigning_sacko` | Lord of the Wastes | bronze | Finished twelfth most recently |

**Three ring colours, not nine.** Gold is the champion and nothing else,
silver is a title worth holding, bronze is one it is not. Nine colours around
a 30px circle would ask it to carry nine meanings; three it can manage, and
the mark overlaid on the sigil says which title. A manager wearing bronze is
wearing it for one of three reasons and the mark separates them.

`sort_order` decides the ring when a manager holds more than one: the throne,
then the best record over all of it, then the best run going on right now.

### Tiebreaks

Every title names its own, because what separates two champions is not what
separates two rivals. **No title is shared** — every rule runs until one
manager is left.

| Title | Tiebreak |
|---|---|
| Protector of the Realm | None needed; one championship game, one winner |
| The Ever-Victorious | Most points scored. 2023 closed with three managers on 18-10 |
| Captain of the Kingsguard | Most points scored **during the streak** |
| Bane of Their Rival | Most meetings, then most points scored **in those meetings** — not a career total, which is mostly games against everybody else |
| Master of Storms | Most points scored |
| The Fortunate / Hounded by Fate | The margins added together, smallest winning: two wins by 0.34 and 0.38 is a closer run than two by 0.23 and 0.75 |
| The Court Fool | **Fewest** points scored during the streak. The one place on the site where less is worse |
| Lord of the Wastes | None needed; one twelfth place |

### The two streak titles are different

They can **sit vacant**, which none of the others can: two straight wins to be
Captain, three straight losses to be Fool. Below that nobody holds it, because
a title that always belongs to somebody says nothing when the best run going
is one game. Across 2022–25 the Captaincy is vacant for one week and the Fool
for six.

**Streaks cross seasons.** Win your last three of one year and your first two
of the next and that is five. Laura's 2024 run is eight straight for this
reason; Carter lost eleven from week 11 of 2024 into week 4 of 2025.

**A retired manager cannot hold either.** Nothing ever ends a departed
manager's run, so without the exemption Theo — two wins to finish 2023, never
played again — would have been Captain through parts of 2025. The exemption is
on the title, not the streak: his games still count for everyone else's.

Regular season only, like every streak rule here. A playoff run is a different
thing and the bracket already says who went furthest.

## Three kinds of title row

`owner_crests.season_year` and `week` mean different things for a title than
for an earned crest. This is the part to understand before touching a query.

| `season_year` | `week` | Meaning | Read by |
|---|---|---|---|
| null | null | Who holds it **now** | The sigil ring, a manager's "titles held" |
| Y | null | Who held it **when Y closed** | The season pages, so 2023's sigils wear 2023's titles |
| Y | W | Who held it **after week W of Y** | Spells, on `/crests` and a manager's page |

The week rows are the history. Titles were once snapshotted only at each
season close, and it recorded almost nothing: Captain of the Kingsguard
changed hands **33 times** across four seasons and the closes caught four of
them. Tom held it in week 9 of 2025 on a five-game run with no record of it
anywhere.

Weeks 15–17 are snapshotted too. Bane of Their Rival counts every meeting and
Tom's fourth against David was the 2023 semifinal; stopping at week 14 lost
the only spell he ever had of it.

`title_spells()` in `app/main.py` folds week rows into unbroken runs, indexed
against the weeks the league actually played rather than week numbers — so the
last week of one season and the first of the next are one spell, and a week
nobody qualified breaks one.

## Schema

```sql
crests        -- the catalogue: code, name, description, category, scope,
              -- standing ('earned' | 'held'), award_mode ('auto' | 'manual'),
              -- colour, sort_order, active
owner_crests  -- who has what: owner_id, crest_id, season_year, week, detail,
              -- awarded_at, awarded_by
```

`detail` is the number that earned it — "182.40, week 6", "5 straight, 680.7
scored" — and is written by the rule, so a rule owns its own wording.

### The unique indexes

Postgres treats nulls as distinct, so one constraint over
`(owner, crest, season, week)` would happily allow the same career crest
twice. Three partial indexes instead:

| Index | Covers | Where |
|---|---|---|
| `owner_crests_career_uniq` | owner, crest | season and week both null |
| `owner_crests_season_uniq` | owner, crest, season | week null |
| `owner_crests_week_uniq` | owner, crest, season, week | week not null |

The third is what lets a partial recompute insert with `on conflict do
nothing` instead of having to delete rows it is about to rewrite identically.

## Recompute

The rules live in **`app/crests.py`**, not in the script, because the app runs
them.

```
compute(conn, seasons, weeks_from=1)  -> {code: [rows]}, dropped
write(conn, awards, season, weeks_from) -> (removed, written)
recompute(conn, season, weeks_from)   -> both, for one season
```

Auto crests are derived, so writing is a **rebuild, not an append**: the rows
in scope are deleted and recomputed. Rows with `award_mode = 'manual'` or
`awarded_by` set are never touched.

**`weeks_from` is the whole trick.** Entering week nine cannot change who held
a title in week eight, so a save recomputes from that week on. `write()`
deletes exactly what `compute()` was asked for — a narrower run must not wipe
rows it is not going to put back.

### When it runs

**Saving scores runs it**, after the commit, so a failure cannot cost the
scores. The page reports either "72 crest rows rewritten" or that it could
not, in which case the hand-run script fixes it.

| Scope | Time |
|---|---|
| A week of the season being played — the weekly case | ~3s |
| Re-entering a week of a finished season | ~13s |
| Everything | ~43s, which is why a save never does one |

It is latency, not work: each `held()` call is eight queries to Neon and a
full run makes several hundred.

### By hand

```
python scripts/award_crests.py                      # everything, dry run
python scripts/award_crests.py --season 2026        # one season, dry run
python scripts/award_crests.py --season 2026 --apply
```

Dry run by default, in the spirit of `resolve_phase.py`. The script is a front
end over `app/crests.py` and shares its rules exactly.

## Artwork

Every earned crest is a **shield**: a field in its category's colour, a dark
edge, a charge in parchment. Every title is a **roundel** in the colour it
rings a sigil with, carrying the same mark. Circles are people and what they
hold; shields are what they won.

Both come from `app/templates/crestart.html` — `crestshield(code, category,
size)` and `crestroundel(code, colour, size)`. There are no image files
anywhere on this site and these are no exception: 28 charges plus the frame,
hand-drawn as SVG paths.

| Category | Field |
|---|---|
| Silverware | gold |
| Over a season | deep blue |
| Week by week | slate |
| Keepers | green |
| The market | bronze |
| Rivalry | oxblood |
| Honours | iron |

Colour, the parchment stroke and the display face are **classes, not
attributes**: an SVG presentation attribute cannot carry a `var()`, and these
have to come from the tokens like everything else.

Sizes: **84px** in the catalogue, **56px** on a manager's page and a season's
honour roll — the height of the two lines of text beside it — and **44px** in
a week panel, where the crests are a footnote to the scores.

Two charges are numerals rather than devices: a Cinzel **3** for Three Oaths
Sworn and **III** for Third of Their Name. Both say it more plainly than any
picture, and both replaced drawings that failed — a laurel wreath was a tulip
filled and a horseshoe stroked.

## Where it all appears

| Page | Shows |
|---|---|
| `/team/<name>` | Titles held, titles once held with their spells, earned crests by category |
| `/season/<year>` | Titles at the close, crests earned that season, and each week's crests under that week's scores |
| `/history` | The titles table: who holds each now, or who held it at any season's close |
| `/crests` | The whole catalogue, won or not, with every award and every spell |
| Everywhere | The ring and mark on a sigil, from the live title row |

`/rules` explains the system — the two kinds, how a title moves, when things
are worked out. What each crest is *for* is only on `/crests`, from the
database.

## Still to do

- Three earned crests have never been awarded and are earnable: Twice
  Crowned, None Were Spared, The Vengeful. The last needs a rivalry week,
  which only exists from 2026.
- **Let the league vote on the four honours.** They are commissioner grants
  today, which is the right mechanism but the wrong decider: the best team
  name and the trade of the year are opinions, and twelve people have them.
  A poll — one round of nominations, one of votes, closing on a date, the
  winner written as an ordinary manual grant with `awarded_by` set to
  whoever ran it — would fit the existing schema without a change to
  `owner_crests`. The grant flow stays either way as the fallback and as the
  thing a poll ultimately calls.
- A season page's crest grid went from three columns to two when the shields
  went in (15rem to 18rem). A 44px shield throughout would win the column
  back.

# We The North — fantasy league site

Handoff document. Everything below reflects the state of the project as built.

Live at `https://wethenorth.onrender.com` · repo `github.com/djans44/WeTheNorth`

---

## 1. Stack and hosting

| Layer | Choice | Notes |
|---|---|---|
| Database | Postgres 18 on **Neon** (free tier) | AWS US East 2 (Ohio). Scales to zero. |
| Backend | **Python 3.12 + FastAPI** | Pinned via `.python-version` |
| Templates | **Jinja2**, server-rendered | No JS framework, no build step |
| Client JS | Plain ES5-style, no dependencies | `app/static/*.js` |
| Migrations | Numbered `.sql` files + a small runner | No ORM, no Alembic |
| Hosting | **Render** free web service | Sleeps after 15 min idle |
| Auth | Email match against `owners.email` | Session cookie, 8 hours |

Deliberately no React, no npm, no Docker.

### Environment variables

| Name | Where | Purpose |
|---|---|---|
| `DATABASE_URL` | `.env` + Render | Neon **pooled** string (host contains `-pooler`) |
| `DATABASE_URL_DIRECT` | `.env` + Render | Neon direct string — used for migrations, since the pooler runs PgBouncer in transaction mode |
| `SECRET_KEY` | `.env` + Render | Signs the session cookie. Local and production values are different. |

`.env` is gitignored. `.env.example` documents the shape.

### Local development

Windows, PowerShell, no WSL.

```powershell
cd $HOME\Documents\WeTheNorth
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

Git identity is set **per-repo**, not globally, so work repos on the same machine
are unaffected. Commits use a GitHub noreply address.

---

## 2. Repository layout

```
app/
  main.py            all routes, queries and helpers (single file)
  templates/         Jinja2 templates
  static/            style.css, app.js, keepers.js
sql/migrations/      001..030, applied in filename order
scripts/             importers and one-off tools
data/                gitignored — source text files and CSVs
.python-version      3.12
requirements.txt
```

### Scripts

| Script | Purpose |
|---|---|
| `migrate.py` | Applies unapplied `sql/migrations/*.sql`, tracked in `schema_migrations` |
| `q.py "<sql>"` | Ad-hoc query or write. **Commits.** Uses the direct connection. |
| `import_matchups.py` | CSV → `matchups` (upsert, handles byes and projections) |
| `import_draft.py` / `import_draft_text.py` | Draft results → `players` + `draft_picks` |
| `import_rosters.py` | Yahoo roster dump → `rosters` |
| `import_keepers.py` / `import_keepers_history.py` | Keeper selections and contracts |
| `import_adp_text.py` | FantasyPros ADP → `player_adp` |
| `import_transactions.py` | Waiver and trade files → `transactions`, then fills `rosters.acquired` |
| `fetch_positions.py` | Pulls the free Sleeper player list, resolves positions, writes `data/positions.csv` |
| `add_players_from_files.py` | Creates missing `players` rows from source text files |
| `export_positions.py` | Dumps current `players` to `data/positions.csv` |
| `resolve_phase.py` | CLI version of keeper phase resolution (dry run by default, `--apply` to write) |

---

## 3. League facts

- **12 teams**, Yahoo Fantasy, **2QB / superflex, half PPR**
- Roster: QB, 2 WR, 2 RB, W/T, W/R/T, **Q/W/R/T**, DEF, 4 BN, 2 IR. **No kicker slot.**
- **13-round draft**, snake
- Seasons in the database: **2022–2026**
- Regular season 14 weeks; playoffs weeks 15–17
- Keepers began in **2023**, 3 per team

### Owners

David (admin), Josh (admin), Tom, Laura, Curtis, Chris, Matt, Tulio, Joey,
Carter, Niall, Borys, Theo (retired after 2023). Niall joined in 2024.

**Tulio has no email on file** and therefore cannot sign in.

### Champions

| Season | Champion | Runner-up | Third | Best record |
|---|---|---|---|---|
| 2022 | Josh | Chris | Joey | Joey 11-3 |
| 2023 | Borys | Tom | Josh | David 10-4 |
| 2024 | Tom | David | Niall | Laura 11-3 |
| 2025 | David | Josh | Niall | David / Josh 9-5 |

No team with the best regular-season record has ever won the title.

---

## 4. Database schema

Migrations `001` to `030`. Every table uses `timestamptz`.

### Core

**`owners`** — `owner_id`, `username`, `email` (nullable), `is_admin`,
`is_retired`, `created_by` (self-FK), timestamps.
Unique indexes on `lower(username)` and `lower(email)`.
Also `last_name` (nullable, feeds derived initials), `avatar_bg` and
`avatar_initials`. `avatar_bg` is **not** unique — colours may be shared.
New owners are created from `/admin/owners` and get **no `teams` row**, so they
stay out of every season-derived page until someone puts them in a season.

**`seasons`** — `season_year` **is** the primary key (smallint, natural key).
`team_count`, `keeper_count`, `is_complete`, `yahoo_league_key`.

**`teams`** — one row per owner per season. `team_id` (surrogate),
`season_year`, `owner_id`, `team_name`.
Unique on `(season_year, owner_id)` **and** on `(team_id, season_year)` — the
second exists so other tables can carry a composite FK that pins a team to its
season.

**`matchups`** — one row per game. `season_year`, `week`, `game_type`,
`team_a_id`, `team_b_id` (nullable = bye), points and projections for each side.

- `check (team_a_id < team_b_id)` — teams are stored in id order, so A-vs-B and
  B-vs-A cannot both exist. Importers sort the pair and swap the scores to match.
- Composite FKs on `(team_id, season_year)` make a cross-season matchup impossible.
- Unique on `(season_year, week, team_a_id, team_b_id)` `nulls not distinct`.
- Null points mean **scheduled but not played**. All stats views exclude them.

`game_type` values: `regular`, `quarterfinal`, `semifinal`, `championship`,
`third_place`, `fifth_place`, `seventh_place`, `ninth_place`,
`eleventh_place`, `consolation`.

### Players, drafts, rosters

**`players`** — `full_name`, `position` in (QB, RB, WR, TE, K, DEF).
Unique on `(lower(full_name), position)`. Defences are stored by nickname
(`Eagles`, `Bills`).

**`draft_picks`** — `season_year`, `round` (1-13), `pick_in_round`, `team_id`,
`player_id`, `is_keeper`.

**`rosters`** — end-of-season roster. PK `(season_year, player_id)`.
`acquired` is `draft` / `waiver` / `trade`, **nullable** = not yet determined.

**`transactions`** — one row per player movement. `kind` is `add` or `trade`;
`method` is `free_agent` / `waiver` / `faab` / `commissioner` for adds.
`from_team_id` is null for adds. Source files carry **no year**, so the season
comes from the import argument and the calendar year from the month
(August onward = `season_year`, earlier = `season_year + 1`).

**`player_adp`** — `(season_year, player_id)`, `adp` as an **overall pick
number**, plus `source` and `captured_on`.

### Keepers

**`keeper_contracts`** — one row per contract. `player_id`, `owner_id`
(**who signed it — never updated**), `original_round`, `signed_season`,
`contract_years` (1 or 3), `contract_round`, `adp_round` (often unknowable),
`status`, `voided_season`, `penalty_round`.

**`keeper_selections`** — one row per player kept per season. `cost_round`,
`keeper_year` (1-4), `contract_id` (null in keeper year one).
Unique on `(season_year, team_id, cost_round)` — two keepers on one team cannot
share a round, which enforces the collision rule structurally.

**`keeper_windows`** — `(season_year, phase)`, `opens_at`, `closes_at`,
`resolved_at`. A phase is closed when an admin resolves it, not when the clock
passes.

**`keeper_plans`** — an owner's intended pick per phase, editable until that
phase resolves.

**`keeper_submissions`** — `(season_year, phase, owner_id)` unique.
`origin` is `manual` / `plan` / `auto` / `forfeit`;
`status` is `pending` / `approved` / `rejected`.

**`keeper_voids`** — `(season_year, contract_id)`. `confirmed_at` null means
declared but not binding.

**`keeper_placeholders`** — hypothetical keepers for draft planning only.
Never affects eligibility or submissions.

### Draft order and rivalries

**`draft_order`** — `(season_year, owner_id)`, `lottery_position`, `slot`.
Unique indexes on both `lottery_position` and `slot` per season.

**`rivalries`** — `(season_year, owner_id)` → `rival_owner_id`.
Written in **both directions**. A unique index on `rival_owner_id` stops anyone
being two managers' rival.

---

## 5. Views

| View | What it gives |
|---|---|
| `team_game_results` | Each matchup flipped into two rows, one per team, with W/L/T |
| `game_log` | `team_game_results` with team and owner names attached |
| `team_season_stats` | Per team per season: W/L/T, PF, PA, `made_playoffs`, `final_rank` |
| `season_results` | Champion, runner-up, third, regular-season leader per season |
| `owner_all_time_stats` | Career record, win %, titles, playoff appearances, best and average finish |
| `owner_head_to_head` | Every owner against every other, all-time |
| `owner_projection_stats` | Actual versus Yahoo projection, regular season only |
| `final_standings` | **Final position 1–12, derived from named placement games** |
| `player_adp_rounds` | `adp_round = ceil(adp / team_count)`; `contract_cost_round = adp_round + 2`, capped at 13 |
| `keeper_cost_basis` | What a player costs to keep, before contracts |
| `keeper_eligibility` | The big one — per player state, cost, contract options |
| `keeper_phase_plan` | Which contract fills which keeper phase |
| `draft_order_state` | Draft order with an `is_on_the_clock` flag |

### Final standings, in detail

Positions are **derived from placement games**, not stored:

| Game type | Winner | Loser |
|---|---|---|
| `championship` | 1st | 2nd |
| `third_place` | 3rd | 4th |
| `fifth_place` | 5th | 6th |
| `seventh_place` | 7th | 8th |
| `ninth_place` | 9th | 10th |
| `eleventh_place` | 11th | 12th |

The playoff bracket has been identical in all four completed seasons:

- **Week 15** — 4 championship-bracket rows (2 games + 2 byes) + 4 consolation
- **Week 16** — 2 semifinals + `fifth_place` + 2 consolation + `eleventh_place`
- **Week 17** — `championship`, `third_place`, `seventh_place`, `ninth_place`

A tie in a placement game would assign the lower position to both teams. Ties
are possible but have never happened.

---

## 6. League rules as implemented

### Keepers

- Up to **3 keepers** from the previous season's end-of-season roster
- **Keeper year 1** — costs the original draft round. Waiver pickups cost round 13.
- **Keeper year 2** — must sign a **1-year** or **3-year** contract
  - 1-year: original cost, then ineligible the next season
  - 3-year: year one at original cost, years two and three at
    `LEAST(original_round, adp_round + 2)`, with ADP **locked at signing**
- **Maximum 4 seasons kept** (keeper year 1 + 3 contract years)
- After a contract expires the player is ineligible for one year, then treated
  as a non-keeper again

### Cost basis

**13 if the player was ever picked up off waivers or free agency that season,
otherwise their draft round.** A trade carries the basis with the player
unchanged. Drafted, dropped, then re-added by the same team is also 13.

### Collisions

Two keepers on one team cannot share a cost round. The later one moves to a more
expensive (earlier) round. A player already under contract cannot move, so the
new player takes the adjustment. **Two round-1 keepers are not permitted** —
there is nowhere earlier to go, and the unique index rejects it.

### Contracts and trades

A contracted player **can be traded and the terms hold**.
`keeper_contracts.owner_id` records who signed it and is never updated; the
current holder comes from `rosters` / `keeper_selections`.

Eligibility comes from the **end-of-season roster**, not from who made the
keeper selection. David traded for Nico Collins mid-2025 while Collins was under
Carter's contract, so David keeps him at Carter's contract round.

### Voids

Contracts are **obligations, not options**. Holding a contracted player at
keeper time means keeping them, and they fill a keeper slot.

The only exit is **voiding**, which:
- frees the slot immediately (still up to 3 keepers that year)
- forces a defence pick at **contract round + 3, capped at 13**
- may be done multiple times per season, each with its own forced pick

Voids have their **own submit action**. A void that is ticked but never
submitted is **discarded** at phase 1 resolution. Submitting a void wipes that
owner's saved plans (with a confirm prompt) because it changes which rounds are
available. Void penalty rounds count as taken, so keepers bump around them.
Phase 1 keeper submission is blocked while a void is ticked but unsubmitted.

The commissioner blocks trades that would give an owner a fourth contracted
player. The system can only warn after the fact.

### Keeper phases

Three sequential phases, each with a submission window.

- Contracts occupy the **earliest phases**, ordered by **remaining contract
  length, longest first**, then earliest cost round, then player name.
  Draft round is irrelevant to the ordering.
- Free choices fill whatever phases remain
- Contract voids happen in **phase 1** only
- Plans **auto-submit** at window close; contracts and forfeits are
  auto-approved, plans land `pending` for review
- Missing a window with no contract to fill the slot **forfeits** that keeper
- After each phase closes, selections are revealed in the draft prep area

**Rejecting** a submission deletes it and reopens the phase. The owner's plan
survives, so they can adjust and resubmit.

### Draft order

1. Admin runs a **randomised lottery** (real lottery rules to come later)
2. That sets the **order of choosing**, not the draft order
3. In one live session, each manager picks whichever **draft slot** they want
4. Admin can pick for anyone or override any slot at any time

Slot choice happens **before** keeper selection.

### Rivalries

One rival per manager per season, symmetric, nobody is two managers' rival.
Auto-generated by scoring every pair on past meetings:

| Game type | Weight |
|---|---|
| regular | 1.0 |
| consolation | 2.0 |
| eleventh / ninth place | 2.5 |
| seventh place | 3.0 |
| fifth place | 3.5 |
| quarterfinal | 5.0 |
| semifinal / third place | 6.0 |
| championship | 10.0 |

Plus **+2** for any game decided by 10 points or fewer, and **+4** for each
prior season the pair were rivals.

The generator evaluates **every perfect matching** (10,395 for 12 owners) and
takes the highest total. Admin can override, with mutual-pairing validation.

### Schedule

14 weeks. Each manager plays **3 opponents twice and 8 once**, choosing the
doubled pairs to favour opponents met least often historically (regular season
only, playoffs excluded).

- **Weeks 1–11** — a full round robin, everyone plays everyone once
- **Week 10** — pinned as **rivalry week**
- **Weeks 12–14** — the rematches
- **No pair meets in consecutive weeks**

Generation uses a random seed shown in the URL, so the preview and the save
produce the same schedule. Saving writes into `matchups` with null scores, which
means the weekly score entry page pre-fills the pairings.

---

## 7. Routes

### Public
| Route | Purpose |
|---|---|
| `GET /` | Landing page and sign-in form |
| `POST /login` | Email match, sets session, redirects to `/current` |
| `GET /logout` | Clears session |
| `GET /health` | `{"status":"ok","database":"connected"}` |

Everything else requires a session (middleware redirects to `/`).

### League
| Route | Purpose |
|---|---|
| `GET /history` | Champions, all-time standings, head-to-head grid, records, projection stats |
| `GET /teams` | All managers |
| `GET /team/{name}` | One manager: season by season, rival, head-to-head, best/worst weeks, projections |
| `GET /seasons` | Season index |
| `GET /season/{year}` | Standings and week-by-week results |
| `GET /current` | Redirects to the newest season |
| `GET /rules` | Placeholder ("the rules are too confusing, just ask Josh") |
| `GET /draft-order`, `POST /draft-order/pick` | Lottery board and slot selection |
| `GET /draft-prep`, `POST /draft-prep/placeholder` | Draft board, keeper columns, placeholders |
| `GET /keepers` | Keeper selection page |
| `POST /keepers/plan` | Save plan (validates round conflicts, refuses to save a conflict) |
| `POST /keepers/submit` | Submit one phase |
| `POST /keepers/void-submit` | Submit voids (writes and confirms in one step) |
| `GET/POST /profile` | Your own name, surname, team name, email and sigil |

### Admin (gated on `is_admin`)
| Route | Purpose |
|---|---|
| `GET/POST /admin/scores` | Weekly score entry, round auto-selected from week number |
| `GET /admin/keepers` | Windows, review queue, voids, resolution preview |
| `POST /admin/keepers/windows` | Save window dates |
| `POST /admin/keepers/resolve` | Resolve a phase (preview first) |
| `POST /admin/keepers/review` | Approve, or reject-and-reopen |
| `GET /admin/keepers/edit/{sid}`, `POST /admin/keepers/edit` | Override a submission |
| `POST /admin/keepers/void` | Cancel, reopen, or add a void |
| `POST /admin/keepers/reset` | Reset one manager or a whole season |
| `GET /admin/rivals`, `POST /admin/rivals/generate`, `POST /admin/rivals/set` | Rivalries |
| `GET /admin/schedule`, `POST /admin/schedule/save` | Schedule generation |
| `GET/POST /admin/owners` | Owners: team name, colour and initials in one pass, plus add an owner |
| `GET/POST /admin/owners/{id}` | One owner: name, team, email, admin, retired, sigil |

---

## 8. Front end conventions

**Theme.** Construction site meets Game of Thrones. A pale stone "proclamation"
panel on a dark iron background, hazard tape framing the title, rivets in the
corners. Serif display face for headings and italic asides, sans for controls.
All colours are CSS variables in `:root` at the top of `style.css`.

**Cache busting.** `static_url()` appends the file's mtime as a query string, so
CSS and JS changes appear without a hard refresh.

**Messages.** `app.js` reads `?msg=` or `?error=` from the URL, shows a bottom
toast (success fades after 4.5s, errors stay until dismissed), strips the params
from the URL, and restores scroll position across form posts.

**No nested forms.** Multiple submit actions on one form use `formaction` on the
button.

---

## 9. Known gaps and next steps

**Loose ends**
- **Tulio has no email** and cannot sign in
- The **landing page still says "under construction"** despite a working site
- Branch protection on `main` is not enabled
- The second contributor has not been invited yet

**Not built**
- **Trades and waivers on the season page** — data is loaded, needs a view and a template
- **League rules content** — the page exists, the rules do not
- 2022–2024 draft `is_keeper` flags are set, but keeper history for those
  seasons could be surfaced on manager pages
- Real **lottery rules** (currently pure randomisation)
- Badges (spec in `docs/features/badges.md`)

**Known rough edges**
- Admin overrides deliberately do **not** validate a round against the
  manager's other phases, so an admin can create a duplicate round. It is
  visible in the Settled table but nothing blocks it.
- Reopening a keeper submission after its phase has resolved leaves the owner
  unable to resubmit, since the window is closed.
- A tie in a placement game would give both teams the lower position.

---

## 10. Gotchas worth knowing

**Render `no-server`.** A plain-text `Not Found` with header
`x-render-routing: no-server` means the free instance is asleep or routing has
not registered. It is never an application error. Wait, retry, or do a
**Clear build cache & deploy**. `x-render-origin-server: uvicorn` confirms a
response actually came from the app. Cold starts can take **50 seconds or more**.

**Neon pooling.** Migrations and multi-statement scripts use
`DATABASE_URL_DIRECT`. The pooled connection runs PgBouncer in transaction mode.

**PowerShell.** `>` redirection writes UTF-16, which Git treats as binary — use
`| Set-Content -Encoding utf8`. Here-strings need `@'` last on its line and `'@`
first on its line. PowerShell has **no triple-quoted strings**. `.Replace()`
fails **silently** when the pattern does not match, so always verify with
`Select-String` afterwards.

**`.gitattributes`** normalises line endings to LF. Without it, Windows CRLF
produces phantom diffs and breaks on Render's Linux boxes.

**Encoding.** Yahoo exports contain curly apostrophes (U+2019) and private-use
characters (U+E000–U+F8FF). Every importer strips both. Team names in the
database use straight apostrophes.

**Name matching.** Player names are normalised by lowercasing, stripping
punctuation, and removing suffixes (Jr, Sr, II, III, IV, V) before comparison.
Defences match on the team nickname.

**FantasyPros ADP** is in `round.pick` format in one column and a plain rank in
another. The rank column is what is imported, by explicit choice. The two
disagree by up to a full round.

**Keeper selections vs rosters.** Selections record who kept a player at the
draft; rosters record who finished the season with them. Eligibility uses
**rosters**. Getting this wrong would have offered Nico Collins to the wrong
manager.

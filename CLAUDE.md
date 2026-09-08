# We The North — Fantasy Football League Site

A season-by-season history and management site for a 12-team Yahoo Fantasy league.
Public pages show history and standings; signed-in owners manage keepers; admins
enter results and run league tooling.

## Stack

- Python + FastAPI, server-rendered Jinja2 templates
- Postgres on Neon (Postgres 18, default branch is named `production`, not `main`)
- No JS framework. Vanilla JS in `static/app.js` only.
- Hosted on Render free tier, auto-deploys from the `main` git branch
- `/health` confirms the DB connection

## Environment constraints

- Local dev is Windows in PowerShell. Prefer running Python directly over
  shell-heavy one-liners; here-strings and `.Replace()` chains have failed
  silently on this machine before.
- **Render's free tier has an ephemeral filesystem.** Anything written to disk is
  lost on every deploy and every idle spin-down. Never persist user data to
  local files — it goes in Postgres.
- Render free tier also cold-starts, so the first request after idle is slow.
  Don't add startup work that assumes a warm process.
- Secrets live in environment variables (`DATABASE_URL`, `SECRET_KEY`). Owner
  email addresses were set directly in Neon and are deliberately not in git.
  Never commit real emails or connection strings.

## Auth

Email-match login: enter an email that matches an `owners` row and you're in. No
password, no email sent. This is a deliberate tradeoff for a 12-person private
league — don't "fix" it into a full auth system without asking. Session cookie
via Starlette `SessionMiddleware`, 8 hour expiry. Sign-in lands on `/current`.

Admin-only routes gate on `owners.is_admin`. David and Josh are admins.

## League structure

- 12 teams. Seasons 2022–2026. Niall joined after 2022; Theo is retired.
- One owner per team per season. Team names change year to year, so anything
  identifying a person must key off `owners`, not team name.
- 14-week regular season, then a fixed playoff bracket that is identical every year:
  - Week 15: 4 quarterfinal rows (2 games + 2 byes) + 4 consolation
  - Week 16: 2 semifinal + fifth_place + 2 consolation + eleventh_place
  - Week 17: championship, third_place, seventh_place, ninth_place
- **Final position 1–12 is derived, not stored.** It comes from the named
  placement games via the `final_standings` view. There is no `final_rank`
  column and there should not be one.
- `matchups` rows are undifferentiated `team_a` / `team_b` — neither side is
  "home". Head-to-head queries must handle both orientations.
- Matchups store projections alongside actual scores.

## Keeper rules

This is the most intricate part of the domain. Get it wrong and the league notices.

- Up to 3 keepers, drawn from the previous season's **end-of-season roster** —
  not from who originally drafted or selected the player. If you traded for a
  contracted player mid-season, you keep him at his existing contract terms.
- Year 1 of keeping costs the player's original draft round. Waiver/FA pickups
  count as round 13.
- Year 2 requires signing a 1-year or 3-year contract.
  - 1-year: original cost, then the player is ineligible the following year.
  - 3-year: year one at original cost; years two and three at
    `LEAST(original round, ADP round + 2)`, with ADP locked at signing.
- Maximum 4 seasons kept total (keeper year 1 + 3 contract years). After a
  contract expires the player is ineligible for one year, then resets to being a
  normal non-keeper.

### Cost basis

13 if the player was picked up off waivers or FA at any point that season,
otherwise their draft round. Trades carry the basis with the player unchanged:
drafted-then-traded keeps the draft round, waiver-then-traded stays 13. Drafted,
dropped, then re-added by the same team is also 13.

### Contracts are obligations, not options

If you hold a contracted player at keeper time you **must** keep him, and he
fills one of your 3 slots. `keeper_contracts.owner_id` records who signed the
contract and is never updated — the current holder comes from `rosters` /
`keeper_selections`. Contracts follow the player, not the owner.

The only exit is voiding. A void frees the slot immediately (still up to 3
keepers that year) and forces a defence pick at contract round + 3, capped at 13.
Multiple voids per season are allowed, each with its own forced defence pick.
The wasted roster spot is the intended cost. The commissioner blocks trades that
would leave an owner holding a 4th contracted player.

### Selection phases

Selection runs in 3 sequential phases, each with its own submission window.

- Contracts occupy the earliest phases automatically, ordered by remaining
  contract length, longest first. Draft round is irrelevant to that ordering.
- Free choices fill whatever phases remain.
- Voids must be declared in phase 1, announced in parallel with that phase's
  selection. Voids have their own submit button and are not locked by the phase 1
  keeper submit. An unsubmitted void is discarded at phase 1 resolution.
  Submitting a void wipes that owner's plans behind a confirm prompt. Phase 1
  keeper submit is blocked while a void is ticked but unsubmitted.
- Owners can plan all three phases in advance. `keeper_plans` holds the intended
  pick per phase; at resolution a plan becomes a submission with `origin='plan'`
  and `status='pending'`. Contracts and forfeits are auto-approved.
- Missing a window with no contract to fill the slot forfeits that keeper.
- Rejecting a submission deletes it and reopens the phase rather than labelling it.
- Owners submit only for themselves. Admins can submit on anyone's behalf.

## Other league machinery

- **Rivalries**: one rival per manager per season, symmetric, nobody is two
  managers' rival. Auto-generated from weighted past meetings, solved as a best
  perfect matching across all pairings. Admin can override with mutual-pairing
  validation.
- **Draft order**: admin-run randomised lottery, then owners pick their draft
  slot in lottery order in one live session. Slot choice happens before keeper
  selection. Admin can pick for anyone or override any slot.
- **Schedule generator**: 14 weeks, each manager plays 3 opponents twice and 8
  once, weighted toward pairings met least often. Weeks 1–11 are a full round
  robin with week 10 pinned as rivalry week; 12–14 are the rematches. No pair
  meets in consecutive weeks. Saves into `matchups` with null scores so the
  score entry page pre-fills pairings.
- **Draft board**: snake order, pick numbers skip keeper cells, and void penalty
  rounds count as taken so keepers bump around them.

## UI conventions

- Server-rendered Jinja templates. Shared components go in `templates/macros.html`.
- User feedback is bottom toasts rendered by `static/app.js`, with scroll
  position preserved across form posts. Do not reintroduce top-of-page banners.
- The visual identity is **Westeros, not a building site**. Stone panels and
  aged parchment on a dark iron ground, Cinzel for display headings, gold used
  as the single accent, and the proclamation voice in the copy — "admitted",
  "the league roll", `.decree`. It used to be a construction-site mashup; the
  hazard tape and rivets are gone and should not come back. Don't drift toward
  generic SaaS cards either.
- Avoid all-caps labels and decorative numbered markers. Display headings are
  uppercase; a table header or a form label is not. Buttons name the action that
  happens, and the resulting toast uses the same verb.
- Empty states are an invitation to act, not an apology.

### The stylesheet

`static/style.css` is one file with **one definition per selector**. It
previously grew to four definitions of `.settled` with three different values
and a hundred byte-identical duplicated lines. If a component needs a variation,
add a modifier class — never repeat the block lower down.

- **Style through the tokens in `:root`.** Colour, type scale, spacing. A raw
  hex or a magic rem in a rule is a bug unless there is a reason in a comment.
- **Namespace anything page-specific.** `.swatch` was already the draft-prep
  legend square when the colour picker reused the name, and the picker silently
  inherited its fixed size. Only genuinely shared things stay bare: `.avatar`,
  `.avatar-line`, `.tm`, `.note`, `.name`.
- **New pages must work on a phone.** There is one `@media (max-width: 48rem)`
  block; put narrow-screen rules there rather than starting a second one. Wide
  tables scroll inside themselves, and the nav has no dropdowns at that width
  because hover does not exist on touch.

## Working here

- Prefer small, reviewable commits. The site owner is experienced with data
  engineering and SQL but new to front-end work, so explain template and CSS
  changes rather than assuming they're self-evident.
- Schema changes go in `migrations/` as plain SQL, applied against Neon by hand.
- When a rule above conflicts with something in the code, ask rather than
  assuming the code is right — several of these rules were decided after the
  first implementation.

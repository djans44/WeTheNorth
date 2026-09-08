# Feature: Owner avatars

Initials in a circle with an owner-chosen background colour. No image uploads —
this is deliberate, since Render's ephemeral filesystem would lose uploaded files
on every deploy and storing image bytes in Postgres is more machinery than this
needs.

## Schema

Built as `sql/migrations/030_owner_avatars.sql`:

```sql
alter table owners
  add column last_name       text,
  add column avatar_bg       text not null default '#33383D',
  add column avatar_initials text;

alter table owners
  add constraint owners_avatar_bg_format
  check (avatar_bg ~ '^#[0-9A-F]{6}$');
```

`avatar_initials` is an override and stays null for most owners. Derive the
default from the owner's name, never from a team name — team names change every
season but the person doesn't.

**`avatar_bg` is deliberately not unique.** An earlier draft of this document
argued the opposite: the head-to-head grid is 12×12, and two managers sharing a
colour makes it harder to read at a glance. That was overruled — colours may be
shared. Two consequences follow, and both are load-bearing:

- The picker does **not** disable taken swatches. It names whoever else already
  sits on a colour underneath it, so the choice is informed rather than blocked.
- The head-to-head **column headers keep the manager's name under the sigil**
  rather than becoming avatar-only. Colour alone can no longer identify a
  manager, so it cannot be the only label.

The format check guards the shape of the value only. Palette membership is
validated in Python against `AVATAR_PALETTE`, so adding or renaming a swatch
never requires a migration.

## Palette

Sixteen fixed swatches for thirteen owners — twelve active plus retired Theo.
Every one is dark enough that parchment text sits on it legibly, which is why no
contrast calculation is needed anywhere in the codebase — the foreground is
always `#EDE4D3`.

The three greys (Charcoal, Stone, Ash) are the ones left unassigned, so every
owner has a distinct hue and the column default is itself a real palette entry.

| Name | Hex | | Name | Hex |
|---|---|---|---|---|
| Oxblood | `#6B1F24` | | Slate | `#2F4A66` |
| Rust | `#8C4425` | | Midnight | `#1E2A44` |
| Antique gold | `#8A6D1F` | | Royal purple | `#472B57` |
| Olive | `#55622F` | | Plum | `#632C51` |
| Forest | `#2C4733` | | Charcoal | `#33383D` |
| Pine | `#1F4A42` | | Stone | `#5E5D55` |
| Deep teal | `#1B5560` | | Bone | `#7A6A55` |
| Northern ice | `#3C6E88` | | Ash | `#4A4E52` |

Keep this list in one place in Python (a module-level constant) and feed both the
picker template and a validation check on save. Do not offer a free hex input —
arbitrary colours will produce unreadable avatars and clash with the site's
stone-and-parchment identity.

## Initials

1. `avatar_initials` if set
2. otherwise first letter of first name + first letter of last name
3. otherwise the first two letters of whatever single name exists

Uppercase on output, capped at 2 characters.

Rung 2 needed a column to derive from. `owners` held only `username`, a single
first name, so **migration 030 adds a nullable `last_name`**. It is null for
everyone today, which puts eleven of the thirteen on rung 3 — and puts **Josh
and Joey both on "JO"**. Those two carry seeded `avatar_initials` of `JS` and
`JY`. Filling in someone's surname promotes them to rung 2 with no code change,
which is the entire reason the column exists.

Rows come back from psycopg as plain dicts, not model objects, so this lives in
`initials_for(username, last_name, override)` in `app/main.py` rather than as a
property. Templates never compute it.

## Rendering

Pure CSS, so nothing is stored or served as an image. One macro in
`templates/macros.html` — which this feature created; CLAUDE.md had named it as
the home for shared components before anything lived there.

`avatar(who, size=32)` takes a **username, an owner_id, or an owner row**, not
an owner object with the columns already attached. It resolves through `av()`,
an environment global backed by a 300-second cache keyed on both owner_id and
lowercased username.

That indirection is what keeps the rollout cheap. Almost every query on this
site selects a username and nothing else — the head-to-head grid is keyed on
username strings, and the stats views are fixed column lists that would each
need redefining to carry the new columns. Looking colours up at render time
means **no existing query or view changed at all**.

Both save paths reset that cache, so a new colour shows on the very next
response rather than up to five minutes later.

A row carrying a second person in another column — an opponent, a rival — must
pass that column explicitly rather than the whole row.

```jinja
{% macro avatar(who, size=32) -%}
  {%- set a = av(who) -%}
  <span class="avatar"
        style="--av-bg: {{ a.bg }}; --av-size: {{ size }}px;
               --av-font: {{ (size * 0.4)|round|int }}px;"
        title="{{ a.name }}">{{ a.initials }}</span>
{%- endmacro %}
```

The CSS below said `var(--serif-display)`, which does not exist. The site's
serif variable is `--display` (`app/static/style.css`).

```css
.avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--av-size);
  height: var(--av-size);
  border-radius: 50%;
  background: var(--av-bg);
  color: #EDE4D3;
  font-family: var(--display);
  font-size: var(--av-font);
  letter-spacing: 0.02em;
  border: 1px solid rgba(196, 168, 110, 0.45);
  flex-shrink: 0;
  user-select: none;
}
```

The thin gold ring reads as a house sigil rather than a chat-app avatar, which
keeps it inside the site's existing visual language.

## Where it appears

Because the macro takes a size, each of these is a one-line change.

Built:

- Nav, beside the signed-in owner's name, linking to `/profile` (24px)
- Manager pages, beside the heading (96px)
- The manager index (32px)
- Season standings (24px)
- All-time standings and the projection table on `/history` (24px)
- Both axes of the head-to-head grid (24px)

Still to do:

- Draft board cells
- Keeper phase cards
- The rivalry pairing display

## Setting the colour

All thirteen owners are seeded with distinct colours in migration 030. A grid
half-full of grey defaults looks broken, and nobody logs in specifically to pick
a colour. Theo is retired but still appears throughout the history pages, so he
gets a colour too — assigned, not self-chosen.

The page that started as a colour picker has since grown into a full profile,
and the admin side split in two:

- **`/profile`** — a signed-in owner sets their own sigil, display name,
  surname, current-season team name, retired flag and sign-in email. Three
  forms, three submits, since CLAUDE.md rules out nested forms. Self-service
  only: no acting-for picker, and no `is_admin` — a self-editable admin flag
  would let any of the twelve make themselves commissioner.
- **`/admin/owners`** — the Owners page. The quick pass is team name, colour and initials
  for everyone in one submit; an **Open** link leads to
  `/admin/owners/{id}` for name, team, email, admin and retired. It also adds
  new owners. This is the only route to Tulio, who has no email and so cannot
  sign in.

Surnames were briefly admin-only and are now on both pages.

Every path validates the submitted hex against `AVATAR_PALETTE` and redirects
with `?error=` on a bad value, matching `admin_rivals_set`. Usernames and emails
are checked against the other owners before the write, so the error can name who
already holds the value rather than surfacing a unique-index violation.

**Adding an owner creates the person, not a team.** Rivalries and the schedule
both take their entrants from the season's `teams` rows and both need an even
count, so a new `owners` row deliberately leaves `teams` alone and stays out of
every season-derived page until someone puts them in a season.

**The league is capped.** An owner can only be added while fewer than
`seasons.team_count` owners are un-retired — the same source `draft_prep` reads
for slot counts, so the cap is not a hardcoded 12. At capacity the form is
replaced by a note pointing at the fix (retire whoever is leaving), and the POST
refuses regardless, so bypassing the form gains nothing. Un-retiring is checked
the same way, since bringing someone back is the other route to thirteen.

The button reads "Save sigil" and the toast reads "Sigil saved". An earlier
draft specified "Colour saved", but the form saves colour *and* initials
together, and CLAUDE.md requires the button and its toast to name the same
action.

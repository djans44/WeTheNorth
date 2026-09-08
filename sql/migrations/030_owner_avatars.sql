-- Owner avatars: a colour and initials per owner, rendered in pure CSS.
-- No image uploads, deliberately -- Render's filesystem is ephemeral.

alter table owners
    add column last_name       text,
    add column avatar_bg       text not null default '#33383D',
    add column avatar_initials text;

-- Palette membership is validated in Python against AVATAR_PALETTE.
-- This only guards the shape, so the schema does not have to change
-- every time a swatch is added or renamed.
alter table owners
    add constraint owners_avatar_bg_format
    check (avatar_bg ~ '^#[0-9A-F]{6}$');

-- Thirteen owners against sixteen swatches. The three greys (Charcoal,
-- Stone, Ash) are left spare so every assigned colour is a distinct hue,
-- and so the column default is itself a real palette entry.
update owners set avatar_bg = v.hex
from (values
    ('David',  '#6B1F24'),  -- Oxblood
    ('Josh',   '#2F4A66'),  -- Slate
    ('Tom',    '#8C4425'),  -- Rust
    ('Laura',  '#472B57'),  -- Royal purple
    ('Curtis', '#55622F'),  -- Olive
    ('Chris',  '#1B5560'),  -- Deep teal
    ('Matt',   '#8A6D1F'),  -- Antique gold
    ('Tulio',  '#1E2A44'),  -- Midnight
    ('Joey',   '#2C4733'),  -- Forest
    ('Carter', '#632C51'),  -- Plum
    ('Niall',  '#3C6E88'),  -- Northern ice
    ('Borys',  '#1F4A42'),  -- Pine
    ('Theo',   '#7A6A55')   -- Bone
) as v (username, hex)
where lower(owners.username) = lower(v.username);

-- last_name is null for everyone, so initials derive from the first two
-- letters of the username -- which makes Josh and Joey collide on "JO".
-- Override those two until real surnames are entered.
update owners set avatar_initials = 'JS' where lower(username) = 'josh';
update owners set avatar_initials = 'JY' where lower(username) = 'joey';

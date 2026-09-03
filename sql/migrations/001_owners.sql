create table owners (
    owner_id   integer     generated always as identity primary key,
    username   text        not null,
    email      text        null,
    is_admin   boolean     not null default false,
    is_retired boolean     not null default false,
    created_by integer     null references owners (owner_id),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index owners_username_lower_key on owners (lower(username));
create unique index owners_email_lower_key    on owners (lower(email));

insert into owners (username, is_admin, is_retired, created_by)
select v.username, v.is_admin, v.is_retired, o.owner_id
from (values
    ('Josh',   true,  false),
    ('Tom',    false, false),
    ('Laura',  false, false),
    ('Curtis', false, false),
    ('Chris',  false, false),
    ('Matt',   false, false),
    ('Tulio',  false, false),
    ('Joey',   false, false),
    ('Carter', false, false),
    ('Niall',  false, false),
    ('Borys',  false, false),
    ('Theo',   false, true)
) as v (username, is_admin, is_retired)
cross join (select owner_id from owners where lower(username) = 'david') as o;

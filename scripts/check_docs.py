# -*- coding: utf-8 -*-
"""Does the documentation still describe the thing that is here?

    python scripts/check_docs.py

Prose rots quietly. A script gets replaced by a page and the checklist goes
on naming the script; a migration adds a table and the schema section never
hears about it; a route is renamed and a feature note points at a URL that
has not existed for months. None of it breaks anything, so nothing complains
-- and the one document written to be read once a year, by somebody who has
forgotten everything, is the one most likely to be wrong when they read it.

Three of those were found by hand in one afternoon. This is that afternoon
written down, so the next one costs a command.

What it checks:

  * every `scripts/x.py`, `app/x.py` and `sql/...` path the docs name exists;
  * every `/route` the docs name is a route the app serves;
  * every script on disk is mentioned somewhere;
  * every table in the database is named somewhere -- skipped, with a note,
    when there is no DATABASE_URL to ask.

What it deliberately does not check: whether the prose is *true*. A row
saying `import_adp_text.py` loads ADP passes here whether or not a page has
since replaced it. That kind of staleness needs a person, and the Instead
column in PROJECT.md is where the answer goes when they find one.

Exits non-zero if anything is unresolved, so it can be a gate rather than a
habit.
"""
import glob
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

DOCS = sorted(glob.glob("docs/*.md") + glob.glob("docs/features/*.md"))

# Named in the docs on purpose while not existing. Each one needs a reason,
# because a list like this is how the rot gets back in.
ALLOWED = {
    # backlog items 1 and 2 describe the plan for the summaries as it was
    # written, not as it was built. The file says so at the top; the script
    # that plan called for was never created, an admin page took its place.
    "scripts/write_summaries.py",
}

# A path in backticks: a file the docs point at.
FILE_REF = re.compile(r"`((?:scripts|app|sql)/[A-Za-z0-9_./-]+)`")
MODULE_REF = re.compile(r"`([a-z_][a-z0-9_]*\.py)`")
ROUTE_REF = re.compile(r"`(/[a-z0-9/_{}-]*)`")
PARAM = re.compile(r"\{[^}]+\}")


def app_routes():
    src = io.open("app/main.py", encoding="utf-8").read()
    return set(re.findall(r'@app\.(?:get|post)\("([^"]+)"', src))


def resolves(ref, routes):
    """Whether a route named in prose is one the app serves.

    Three ways to match, and the third is the one that needs saying. The docs
    write `/season` and `/team` in running text about pages whose routes take
    a parameter, and that is not a stale pointer -- it is how anybody refers
    to the page. So a reference that is a prefix of a real route, at a path
    boundary, counts.
    """
    bare = PARAM.sub("", ref).rstrip("/")
    for r in routes:
        if ref == r:
            return True
        if bare and PARAM.sub("", r).rstrip("/") == bare:
            return True
        if bare and r.startswith(bare + "/"):
            return True
    return False


def main():
    routes = app_routes()
    problems = []

    for doc in DOCS:
        for n, line in enumerate(io.open(doc, encoding="utf-8").read().splitlines(), 1):
            where = "%s:%d" % (doc.replace("\\", "/"), n)
            for ref in FILE_REF.findall(line):
                if ref not in ALLOWED and not os.path.exists(ref):
                    problems.append((where, "no such file", ref))
            for ref in MODULE_REF.findall(line):
                if any(os.path.exists(b + ref) for b in ("scripts/", "app/", "")):
                    continue
                if any(a.endswith("/" + ref) for a in ALLOWED):
                    continue
                problems.append((where, "no such module", ref))
            for ref in ROUTE_REF.findall(line):
                if not resolves(ref, routes):
                    problems.append((where, "no such route", ref))

    prose = " ".join(io.open(d, encoding="utf-8").read() for d in DOCS)
    for path in sorted(glob.glob("scripts/*.py")):
        name = os.path.basename(path)
        # A leading underscore says "not a thing you run".
        if name.startswith("_") or name in prose:
            continue
        problems.append(("scripts/", "documented nowhere", name))

    missing_tables = check_tables(prose)
    for t in missing_tables or ():
        problems.append(("database", "documented nowhere", t))

    print("%d docs, %d routes%s"
          % (len(DOCS), len(routes),
             "" if missing_tables is not None else ", database not checked"))
    if not problems:
        print("Everything the docs name is there, and everything there is named.")
        return 0
    print()
    for where, why, what in problems:
        print("  %-28s %-18s %s" % (where, why, what))
    print()
    print("%d unresolved." % len(problems))
    return 1


def check_tables(prose):
    """Tables in the database that no document mentions, or None if unasked.

    Needs a connection, and the point of the rest of this is that it runs
    anywhere. So a missing DATABASE_URL is a skipped check and a line saying
    so, not a failure: a check that cannot run on a laptop is a check nobody
    runs.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
        import psycopg
    except ImportError:
        return None
    url = os.environ.get("DATABASE_URL_DIRECT") or os.environ.get("DATABASE_URL")
    if not url:
        return None
    try:
        # A short timeout, because the alternative is what happened the first
        # time this was tested against an unreachable host: psycopg sat there
        # for two minutes while a check that is supposed to take a second did
        # nothing. Unreachable is a skipped check, and it should say so fast.
        with psycopg.connect(url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                select table_name from information_schema.tables
                where table_schema = 'public' and table_type = 'BASE TABLE'
            """)
            tables = [r[0] for r in cur.fetchall()]
    except Exception as e:
        print("could not reach the database: %s" % str(e).splitlines()[0])
        return None
    # schema_migrations is migrate.py's own bookkeeping and is described
    # there rather than as part of the league's schema.
    return sorted(t for t in tables
                  if t != "schema_migrations" and ("`%s`" % t) not in prose)


if __name__ == "__main__":
    sys.exit(main())

"""Store a summary that was written rather than generated.

    python scripts/write_summary.py --season 2023 --kind preview --file body.txt
    python scripts/write_summary.py --season 2023 --kind week --week 7 --file body.txt
    python scripts/write_summary.py --season 2023 --kind preview --file body.txt --publish

The summaries table records which model wrote each row, and these rows say
`claude-opus-5` for the same reason the generated ones say `gemini-3.6-flash`:
the record of who wrote a thing is worth more than a tidy single value, and
the point of writing the 2022-2025 backlog by hand is to have something to
measure the generated ones against.

Unpublished unless --publish is passed. Nothing reaches the season page until
it is published, and that stays true for these.

At most two summaries exist for any one thing written about: a draft and a
published one. Writing replaces whichever of the two this row is, which for
--publish means the text the league could already read is gone.

    --facts   print what a summary of that shape is allowed to talk about and
              stop. The same facts the prompt hands the model, so writing one
              by hand and generating one are working from the same notes.
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), ".env"))

import psycopg  # noqa: E402

from app import summaries  # noqa: E402

AUTHOR = "claude-opus-5"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--kind", required=True,
                    choices=("preview", "week", "season"))
    ap.add_argument("--week", type=int)
    ap.add_argument("--file", help="UTF-8 text, paragraphs separated by a blank line")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--facts", action="store_true",
                    help="print the facts for this summary and stop")
    ap.add_argument("--model", default=AUTHOR)
    args = ap.parse_args()

    if args.kind == "week" and not args.week:
        ap.error("--week is required for a week summary")
    if not args.facts and not args.file:
        ap.error("--file is required unless --facts")

    with psycopg.connect(os.environ["DATABASE_URL"],
                         row_factory=psycopg.rows.dict_row) as conn:
        def q(sql, params):
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]

        if args.facts:
            if args.kind == "preview":
                facts = summaries.preview_facts(q, args.season)
            elif args.kind == "season":
                facts = summaries.season_facts(q, args.season)
            else:
                facts = summaries.week_facts(q, args.season, args.week)
            print(summaries.as_text(facts))
            return

        body = io.open(args.file, encoding="utf-8").read().strip()
        if not body:
            ap.error("%s is empty" % args.file)

        # Two per thing written about, one of each state -- see migration
        # 045. Whichever state this row is going into, the row already in it
        # makes way.
        with conn.cursor() as cur:
            cur.execute("""
                delete from summaries
                where season_year = %s and kind = %s
                  and week is not distinct from %s
                  and matchup_id is null
                  and (published_at is not null) = %s
            """, (args.season, args.kind, args.week, args.publish))
            replaced = cur.rowcount
            cur.execute("""
                insert into summaries
                    (season_year, kind, week, body, model, published_at)
                values (%s, %s, %s, %s, %s, case when %s then now() end)
                returning summary_id
            """, (args.season, args.kind, args.week, body, args.model,
                  args.publish))
            new_id = cur.fetchone()["summary_id"]
        conn.commit()

    print("%s %s%s for %s: id %d, %d characters, %s%s"
          % (args.model, args.kind,
             " %d" % args.week if args.week else "", args.season, new_id,
             len(body), "published" if args.publish else "unpublished",
             ", replacing the one that was there" if replaced else ""))


if __name__ == "__main__":
    main()

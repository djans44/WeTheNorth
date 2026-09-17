"""Generated prose about the league.

Domain logic in its own module, like app/crests.py: the web app calls in, and
nothing here imports FastAPI.

The model is called over plain HTTPS rather than through a client library.
The request is one POST with a JSON body, the project carries twenty-odd
dependencies and no HTTP client beyond urllib, and adding an SDK to Render for
one endpoint would cost more than it saves.
"""
import json
import os
import time
import urllib.error
import urllib.request

# Pinned rather than an alias. `gemini-flash-latest` would move under us, and
# the summaries table records which model wrote each row -- a name that means
# something different next month makes that record a lie.
#
# 2.5-flash was the obvious choice and is refused for a key created today:
# "no longer available to new users". It is still listed by the models
# endpoint, so being listed is not the same as being usable, and the only
# way to find out is to send something.
MODEL = "gemini-3.6-flash"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
TIMEOUT = 45

# The house voice, shared by every prompt so they cannot drift apart.
VOICE = """You are writing for We the North, a twelve-team fantasy football
league that has run since 2022 and dresses itself in the language of Westeros:
stone and parchment, managers rather than users, a keeper "round" rather than a
phase, sigils and crests and titles.

Write like a league historian with a dry sense of humour. Specific, not
breathless. You may reach for the register occasionally -- "the league roll",
"admitted", "the choosing" -- but a whole paragraph of it is exhausting, so
earn it and move on.

Rules:
- Use the facts given and nothing else. Do not invent scores, records,
  players or events. If something is not in the facts, it did not happen.
- Do not claim a superlative -- most, best, highest, first, only -- unless a
  fact says so in those words. A sorted list does not tell you who leads it.
- Do not convert a count into an ordinal. "kept nobody in 2 rounds" is not
  "forfeited the second round".
- The facts are notes, not prose. Say them in your own words. Never lift a
  line verbatim -- "finished rank 1" is a database field, "took the title"
  is writing.
- Do not list everyone when a few will do. Twelve managers named in a row is
  a table, and the reader already has the table.
- Name managers by name. They know each other.
- No headings, no bullet points, no markdown. Plain paragraphs separated by
  a blank line.
- Do not open with "In a league where" or any variant. Start with something
  that happened.
"""


def available():
    """Whether a key is configured at all. Cheap, and worth asking before
    building a prompt nobody can send."""
    return bool(os.environ.get("GEMINI_API_KEY"))


class SummaryError(Exception):
    pass


# 503 "experiencing high demand" and 429 are transient and common: writing
# this, two of three attempts in a row were refused with 503 and the third
# succeeded unchanged. A summary is triggered by a score entry and nobody is
# watching it, so a couple of retries here is the difference between usually
# working and usually not.
RETRY_ON = (429, 500, 502, 503, 504)
ATTEMPTS = 3
BACKOFF = 8


def ask(prompt, temperature=0.8):
    """One summary, retrying the failures that are worth retrying. Raises
    SummaryError with something readable otherwise, because every caller is
    wrapping this and showing it to an admin."""
    last = None
    for attempt in range(ATTEMPTS):
        try:
            return _ask_once(prompt, temperature)
        except SummaryError as e:
            last = e
            if getattr(e, "retryable", False) and attempt < ATTEMPTS - 1:
                time.sleep(BACKOFF * (attempt + 1))
                continue
            raise
    raise last


def _ask_once(prompt, temperature):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SummaryError("GEMINI_API_KEY is not set in this environment.")

    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }).encode("utf-8")

    req = urllib.request.Request(
        ENDPOINT % MODEL, data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key})

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8"))["error"]["message"]
        except Exception:
            pass
        err = SummaryError("Gemini returned %s. %s" % (e.code, detail[:200]))
        err.retryable = e.code in RETRY_ON
        raise err
    except Exception as e:
        # A timeout or a dropped connection is worth one more go.
        err = SummaryError("Could not reach Gemini: %s" % str(e)[:160])
        err.retryable = True
        raise err

    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError):
        # A blocked or empty response has no parts. Say which, because the
        # two want different fixes.
        reason = (data.get("promptFeedback", {}).get("blockReason")
                  or (data.get("candidates") or [{}])[0].get("finishReason")
                  or "no reason given")
        raise SummaryError("Gemini returned nothing (%s)." % reason)

    if not text:
        raise SummaryError("Gemini returned an empty summary.")
    return text


# ---- facts -------------------------------------------------------------
#
# Each of these gathers what one kind of summary is allowed to talk about.
# The prompt says to use these and nothing else, so anything missing here is
# a thing the model cannot mention -- which is the intended failure, rather
# than it filling the gap with something plausible and wrong.


def preview_facts(q, season):
    """Where a season stands before a ball is thrown: who is in it, what they
    kept, where they draft from, and who they are set against."""
    facts = {"season": season}

    facts["teams"] = q("""
        select o.username as manager, t.team_name
        from teams t join owners o on o.owner_id = t.owner_id
        where t.season_year = %s order by o.username
    """, (season,))

    # keeper_submissions, not keeper_selections. Selections is the archive and
    # is not written until later -- 2026 has three resolved rounds and zero
    # rows in it -- so the season being previewed is precisely the one it
    # cannot answer for.
    facts["keepers"] = q("""
        select o.username as manager, p.full_name as player,
               s.cost_round, s.phase as keeper_round
        from keeper_submissions s
        join owners o on o.owner_id = s.owner_id
        join players p on p.player_id = s.player_id
        where s.season_year = %s and s.status = 'approved'
        order by o.username, s.phase
    """, (season,))

    # Forfeiting a keeper round means keeping nobody in it, and it is the
    # sort of decision a preview should notice.
    #
    # Written as a sentence rather than a count under a heading. The first
    # version of this returned `rounds_forfeited: 3` for Carter under the
    # heading "Forfeited Rounds", and the model read the count as a round
    # number and the whole thing as a draft-pick penalty -- producing "Carter
    # yields his pick in the third round", which is wrong twice over. The
    # facts have to be unambiguous to a careful reader, because that is all
    # the model is.
    facts["keeper_rounds_nobody_was_kept_in"] = [
        {"note": "%s kept nobody in %d of their 3 keeper rounds"
                 % (r["manager"], r["n"])}
        for r in q("""
            select o.username as manager, count(*) as n
            from keeper_submissions s
            join owners o on o.owner_id = s.owner_id
            where s.season_year = %s and s.origin = 'forfeit'
            group by o.username order by 2 desc, 1
        """, (season,))]

    facts["draft_slots"] = q("""
        select o.username as manager, d.slot
        from draft_order d join owners o on o.owner_id = d.owner_id
        where d.season_year = %s and d.slot is not null
        order by d.slot
    """, (season,))

    facts["rivalries"] = q("""
        select distinct least(a.username, b.username) as one,
                        greatest(a.username, b.username) as two
        from rivalries r
        join owners a on a.owner_id = r.owner_id
        join owners b on b.owner_id = r.rival_owner_id
        where r.season_year = %s
    """, (season,))

    # How last year finished, so the preview can say who is defending what.
    # team_season_stats rather than final_standings: the latter is only
    # position and name, and a preview wants the records behind them.
    facts["last_season"] = q("""
        select season_year, username as manager, final_rank,
               wins, losses, round(points_for) as points_for
        from team_season_stats
        where season_year = %s - 1
        order by final_rank
    """, (season,))

    # The titles as they stand going in. A title is held by one manager at a
    # time and rings their sigil everywhere it is drawn, so who carries what
    # into a season is the standing of the realm, and a preview that does not
    # mention them is describing a different league.
    #
    # season_year is null for the live holder; the same crest also has a row
    # per closed season saying who held it then, which is not what a preview
    # about the year ahead wants.
    facts["titles_held_going_in"] = [
        {"note": "%s holds %s (%s)" % (r["username"], r["name"], r["detail"])
                 if r["detail"] else "%s holds %s" % (r["username"], r["name"])}
        for r in q("""
            select cr.name, o.username, oc.detail
            from owner_crests oc
            join crests cr on cr.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            where cr.standing = 'held' and oc.season_year is null
            order by cr.sort_order, cr.name
        """, ())]

    # What last season handed out. Earned crests are kept for good, so these
    # are the honours the year just gone is remembered by.
    facts["crests_won_last_season"] = [
        {"note": "%s won %s%s" % (r["username"], r["name"],
                                  " -- %s" % r["detail"] if r["detail"] else "")}
        for r in q("""
            select cr.name, o.username, oc.detail
            from owner_crests oc
            join crests cr on cr.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            where oc.season_year = %s - 1 and cr.standing = 'earned'
              and oc.week is null
            order by cr.sort_order, cr.name
        """, (season,))]

    # Superlatives, stated rather than left to be worked out. Given twelve
    # rows of points_for the model claimed the second-highest was the
    # highest -- a sorted list is not the same as being told who won it, and
    # "most points" is exactly the sort of claim a reader will check.
    facts["last_season_superlatives"] = [
        {"note": "%s scored the most points in %d with %d"
                 % (r["manager"], season - 1, r["points_for"])}
        for r in q("""
            select username as manager, round(points_for) as points_for
            from team_season_stats
            where season_year = %s - 1
            order by points_for desc limit 1
        """, (season,))]
    return facts


def as_text(facts):
    """Facts as compact lines. Readable by a person, which matters when a
    summary comes out wrong and the question is whether the prompt was
    wrong or the data was."""
    out = []
    for key, rows in facts.items():
        if key == "season":
            out.append("Season: %s" % rows)
            continue
        if not rows:
            out.append("\n%s: none recorded" % key.replace("_", " ").title())
            continue
        out.append("\n%s:" % key.replace("_", " ").title())
        for r in rows:
            out.append("  " + ", ".join(
                "%s %s" % (k, v) for k, v in r.items() if v is not None))
    return "\n".join(out)


def preview_prompt(facts):
    return """%s

Write a season preview for %s: four or five paragraphs.

Cover the shape of the year: who carries which titles into it, three or four
keeper decisions worth remarking on rather than all twelve, roughly how the
draft board falls, and a rivalry or two worth watching. Look forward -- but do
not predict results as though they have already happened.

Titles and crests are the league's own furniture and should feel like it. A
title is held by one manager at a time and rings their sigil wherever it is
drawn, so carrying one into a season means something; an earned crest is kept
for good. Name them as the league names them -- Protector of the Realm, The
Court Fool -- rather than describing them generically.

The facts:
%s
""" % (VOICE, facts["season"], as_text(facts))

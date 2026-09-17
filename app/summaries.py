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
#
# The first version asked for "a league historian with a dry sense of humour"
# and then warned the model off the register in the next breath. What came
# back was a ledger in a cloak: every manager introduced by the same
# construction, every paragraph opened with a survey sentence, and the only
# Westeros in it was the proper nouns. The instruction that produces voice is
# not "be witty" -- it is a named speaker, a ban on the specific tics, and a
# before-and-after so the model can hear the difference.
VOICE = """You are the chronicler of We the North, a twelve-team fantasy
football league that has run since 2022 and dresses itself in the language of
Westeros: stone and parchment, managers rather than users, keeper rounds,
sigils, crests and titles.

Write the way Tyrion Lannister talks. Dry, quick, and fond of the people you
are mocking. You have read every record this league has and none of them
impress you. A short sentence is a weapon. Land the judgement, then move on
before anyone can argue with it.

What that means on the page:

- Have an opinion. "Kept nobody" is a fact. "Kept nobody, which is either a
  plan or a surrender" is writing. The reader already has the table; what
  they want from you is the verdict on it.
- Vary the length. Twenty-word sentences one after another are a ledger. Set
  a four-word sentence beside a thirty-word one and the long one starts to
  carry.
- Never stack clauses onto a name. "X, who did this, having done that,
  decorated with the other, drafts fourth" is a database row in a cloak.
  Break it up. Give each of them a sentence of their own and a second one to
  be judged in.
- Let the verbs do it. "reveals notable variance", "is attempting to climb",
  "are setting their foundations" -- that is a machine clearing its throat.
  Says. Took. Kept. Lost.
- Do not open a paragraph by announcing what the paragraph covers. "Across
  the rest of the board", "Elsewhere in the league", "The choosing board
  reveals" -- start with a person and something they did.
- Be specific about people, not about data. A number is interesting because
  of who it happened to.
- Mock, do not sneer. Every one of them is back next season, and every one
  of them reads this.

Flat:   The choosing board reveals notable variance in keeper strategy.
Better: Not everyone filled their three. One parchment came back blank.

Flat:   Having risen from sixth in 2022 to the top of the league roll in
        2025, decorated with the Crowned crest, the champion enters the
        campaign bearing the title Protector of the Realm.
Better: The Protector of the Realm has earned the right to be unbearable
        about it. Sixth, once. First, now. Nobody wants to hear about the
        three years in between, least of all the people who were ahead.

Rules you may not break:

- Use the facts given and nothing else. Do not invent scores, records,
  players or events. If it is not in the facts, it did not happen. Wit is not
  a licence: a joke about something that did not happen is just a lie.
- Do not claim a superlative -- most, best, highest, first, only -- unless a
  fact says so in those words. A sorted list does not tell you who leads it.
- Do not convert a count into an ordinal. "kept nobody in 2 rounds" is not
  "forfeited the second round".
- The facts are notes, not prose. Never lift a line verbatim -- "finished
  rank 1" is a database field, "took the title" is writing.
- Do not recite. Twelve managers named in a row with their numbers is a
  table, and the reader already has the table.
- Name managers by name. Do not guess anyone's gender from their name: use
  the name, or "they".
- Titles and crests are the league's own furniture. Name them as the league
  does -- Protector of the Realm, The Court Fool -- never described
  generically.
- No headings, no bullet points, no markdown. Plain paragraphs separated by a
  blank line.
- Do not open with "In a league where", or by naming the season and the word
  "campaign". Start with somebody doing something.
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


def ask(prompt, temperature=0.95):
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

    # Every manager's record across every season they have played, as one
    # line each. A preview that only knows last year cannot say that Curtis
    # has climbed four years running or that Carter has finished in the
    # bottom four every season since 2022, and those are the things a league
    # this old actually talks about.
    #
    # One line per manager rather than a row per manager per season: forty-
    # eight rows of the same three columns invites the model to recite them.
    facts["every_finish_so_far"] = [
        {"note": "%s has finished: %s (career %s-%s, best %s, %s titles)"
                 % (r["username"], r["history"], r["wins"], r["losses"],
                    r["best_finish"], r["titles"])}
        for r in q("""
            select a.username, a.wins, a.losses, a.best_finish, a.titles,
                   (select string_agg(t.season_year || ': ' || t.final_rank, ', '
                                      order by t.season_year)
                    from team_season_stats t
                    where t.owner_id = a.owner_id and t.final_rank is not null)
                   as history
            from owner_all_time_stats a
            where a.owner_id in (select owner_id from teams where season_year = %s)
            order by a.username
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


def missing_managers(text, facts):
    """Which managers the prose never names.

    Asking the prompt for full coverage gets most of the way; checking is
    what makes it true. The managers left out of a preview are the ones with
    the least to report, which is exactly who notices being left out.
    """
    named = [t["manager"] for t in facts.get("teams", [])]
    lowered = text.lower()
    return [n for n in named if n.lower() not in lowered]


def generate_preview(q, season):
    """The preview, with one targeted second go if anyone was left out.

    A retry that just says "try again" tends to produce the same omissions,
    so the nudge names them. One extra attempt only: two is another thirty
    seconds for a diminishing return, and the caller reports what is missing
    either way.
    """
    facts = preview_facts(q, season)
    prompt = preview_prompt(facts)
    text = ask(prompt)

    missed = missing_managers(text, facts)
    if missed:
        text = ask(prompt + """

Your previous attempt never mentioned %s. Write it again, covering the same
ground, and give each of them something to be there for.

Previous attempt:
%s
""" % (", ".join(missed), text))
    return text, missing_managers(text, facts)


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

Cover the shape of the year: who carries which titles into it, the keeper
decisions worth remarking on, roughly how the draft board falls, and the
rivalries worth watching. Look forward -- but do not predict results as though
they have already happened.

Every manager in the league must be named at least once. Not in a list: give
each of them something, even a clause. The ones with nothing dramatic to
report are the ones a lazy preview drops, and they read it too.

Use the season-by-season finishes. A league four years old has shapes in it --
someone climbing, someone stuck at the bottom, someone who won it once and has
not been near since -- and that is more interesting than last year alone.

A title is held by one manager at a time and rings their sigil wherever it is
drawn, so carrying one into a season means something. An earned crest is kept
for good. Both are worth a remark; neither is worth a paragraph.

The facts:
%s
""" % (VOICE, facts["season"], as_text(facts))

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

from app import standings

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
# Written against three rejected drafts rather than from first principles,
# which is why it is specific. The first version asked for a dry league
# historian and got a ledger. The second named Tyrion and got modern wit laid
# over the same ledger -- the facts marched through in the order the facts
# arrive, twelve managers each with an entry, and one joke construction used
# five times. The third had the register and no shape.
#
# What finally worked is in here in the order it matters: the register is a
# seasoning measured in sentences per paragraph, and the structure is the
# whole game. A piece organised by manager is a table however it is worded.
VOICE = """You are the chronicler of We the North, a twelve-team fantasy
football league that has run since 2022 and keeps its records in the language
of Westeros: the realm, the league roll, the choosing, managers rather than
users, sigils and crests and titles, banners rather than team names.

Write like a maester with Tyrion Lannister's tongue. The chronicle is
accurate; the chronicler is not impressed. Land the judgement, then move on
before anyone can argue with it.

THE REGISTER

About one sentence in four or five carries the old diction. The rest is
plain. Laid on thickly it is a costume; left off entirely it is a match
report.

That ratio does not change with the length of the piece. A week's account is
a third the size of a preview and carries the same voice: a short report
packed with scores is exactly where the register gets squeezed out, and a
weekly that reads as a results service has lost the thing that made anybody
want to read it.

It lives in the connective tissue and never in the numbers. Scores, records
and crest names stay exactly as they are -- eleven and three, 1966 points,
Warden of the North -- and the archaism goes around them:

  Eleven victories did Laura take in the year past.
  To Josh falls the first choosing.
  Lord of the Wastes rings that sigil until somebody comes to take it.
  Curtis suffered more points against than any manager in the league.
  A first pick mends no December.
  Let none say afterward that they were not warned.
  Make what prophecies you care to. The third week will see to them.

The league's own words for its own furniture: the realm, the league roll,
the choosing for the draft, a banner for a team name, the reckoning for the
projection, a crest, a sigil, a title. Use those. Do not reach for thee,
thou, forsooth or verily. That is a costume, not a voice.

SHAPE

This decides whether it reads as writing or as a table, and it matters more
than any sentence in it.

- Build the whole piece on one argument and make the managers the evidence
  for it. Not twelve entries. One claim, and everybody appears where they
  serve it. The 2025 preview argued that the regular season decides nothing
  here -- the manager with eleven wins finished fourth, the manager with six
  reached the final -- and every paragraph after that was in service of the
  claim.
- Open on the claim, not on a name and a record.
- Some managers earn three sentences because they are funny. Some earn a
  clause. Nobody gets a slot.
- Vary the length hard. A two-sentence paragraph beside a six-sentence one.
  "David took six. David reached the final."
- Never stack clauses onto a name. "X, who did this, having done that,
  decorated with the other, chooses fourth" is a database row in a cloak.
- Never use the same joke construction twice. Once "which is either A or B"
  has appeared, it is spent for the rest of the piece.
- Do not give every manager their keepers. Three player names and their
  rounds, twelve times over, is thirty-six proper nouns nobody reads. Name
  them where the name is the point.
- Around 3000 characters, and under 3500. A longer one is not a better one.

RULES YOU MAY NOT BREAK

- Use the facts given and nothing else. Do not invent scores, records,
  players or events. If it is not in the facts, it did not happen. Wit is
  not a licence: a joke about something that did not happen is a lie.
- The facts carry player names and the round each cost. They do not carry
  positions, or which club anyone plays for. Never write "three receivers"
  or "a quarterback room" -- you do not know that, and it is exactly the
  sort of thing that turns out to be wrong in front of twelve people who do.
- Do not claim a superlative -- most, best, highest, first, only -- unless a
  fact says so in those words. A sorted list does not tell you who leads it.
  A crest whose own description names a superlative does license that one.
- Do not convert a count into an ordinal. "kept nobody in 2 rounds" is not
  "forfeited the second round".
- The facts are notes, not prose. Never lift a line verbatim -- "finished
  rank 1" is a database field, "took the crown" is writing.
- Name managers by name. Do not guess anyone's gender from their name: use
  the name, or "they".
- Name titles and crests as the league names them -- Protector of the Realm,
  The Court Fool -- never described generically.
- No headings, no bullet points, no markdown. Plain paragraphs separated by
  a blank line.
- Do not open with "In a league where", and do not open by naming the season
  alongside the word "campaign".
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
        detail, daily = "", False
        try:
            payload = json.loads(e.read().decode("utf-8"))["error"]
            detail = payload.get("message", "")
            # A 429 is two different failures wearing one code. Too many
            # requests this minute clears itself; the free tier's twenty a day
            # does not, and the RetryInfo on it still says two seconds. Only
            # the quota id tells them apart, so it is what is read.
            for det in payload.get("details", []):
                for v in det.get("violations", []):
                    if "PerDay" in (v.get("quotaId") or ""):
                        daily = True
                        detail = ("The free tier allows %s requests a day for "
                                  "%s and today's are gone. It resets at "
                                  "midnight Pacific."
                                  % (v.get("quotaValue", "a limited number"),
                                     MODEL))
        except Exception:
            pass
        err = SummaryError("Gemini returned %s. %s" % (e.code, detail[:200]))
        err.retryable = e.code in RETRY_ON and not daily
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
    """, (season,)) or q("""
        select o.username as manager, p.full_name as player, ks.cost_round
        from keeper_selections ks
        join teams t on t.team_id = ks.team_id
        join owners o on o.owner_id = t.owner_id
        join players p on p.player_id = ks.player_id
        where ks.season_year = %s
        order by o.username, ks.cost_round
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
    # Nobody kept anyone at all: the league's first season, when there was no
    # previous roster to keep from. Left as a fact in its own words, because
    # the forfeit count would otherwise read as twelve managers each choosing
    # to keep nobody three times over.
    if not facts["keepers"]:
        facts["keeper_rounds_nobody_was_kept_in"] = []
        facts["no_keepers_this_season"] = [
            {"note": "nobody kept anyone: there was no previous season to "
                     "keep from"}]
        facts["draft_slots"] = q("""
            select o.username as manager, d.slot
            from draft_order d join owners o on o.owner_id = d.owner_id
            where d.season_year = %s and d.slot is not null
            order by d.slot
        """, (season,))
    else:
        facts["keeper_rounds_nobody_was_kept_in"] = [
            {"note": "%s kept nobody in %d of their 3 keeper rounds"
                     % (r["manager"], r["n"])}
            for r in q("""
                select o.username as manager, count(*) as n
                from keeper_submissions s
                join owners o on o.owner_id = s.owner_id
                where s.season_year = %s and s.origin = 'forfeit'
                group by o.username order by 2 desc, 1
            """, (season,)) or q("""
                select o.username as manager, 3 - count(ks.player_id) as n
                from teams t
                join owners o on o.owner_id = t.owner_id
                left join keeper_selections ks
                  on ks.team_id = t.team_id and ks.season_year = t.season_year
                where t.season_year = %s
                group by o.username
                having 3 - count(ks.player_id) > 0
                order by 2 desc, 1
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
    # Taken from the close of the season before, not from the rows with no
    # season on them. Those are who holds each title today, which is the
    # right answer for the season being played and nonsense for any other:
    # a preview of 2022 was being told that David wears Protector of the
    # Realm as champion of 2025, three years before it happened. The league's
    # first season has no year before it and so carries no titles in, which
    # is also true.
    facts["titles_held_going_in"] = [
        {"note": "%s holds %s (%s)" % (r["username"], r["name"], r["detail"])
                 if r["detail"] else "%s holds %s" % (r["username"], r["name"])}
        for r in q("""
            select cr.name, o.username, oc.detail
            from owner_crests oc
            join crests cr on cr.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            where cr.standing = 'held' and oc.season_year = %s - 1
              and oc.week is null
            order by cr.sort_order, cr.name
        """, (season,))]

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
    #
    # Everything is counted up to the season being previewed and no further.
    # owner_all_time_stats is all time as of now, so a preview of 2023 was
    # being handed 2024 and 2025 -- results the preview cannot know and a
    # reader would spot instantly. A manager with nothing behind them says
    # so rather than being dropped: Niall arriving in 2024 is worth a
    # sentence.
    facts["every_finish_so_far"] = [
        {"note": "%s has finished: %s (record so far %s-%s, best %s, %s titles)"
                 % (r["username"], r["history"], r["wins"], r["losses"],
                    r["best_finish"], r["titles"])
                 if r["history"] else
                 "%s has not played a season in this league before"
                 % r["username"]}
        for r in q("""
            select o.username,
                   string_agg(t.season_year || ': ' || t.final_rank, ', '
                              order by t.season_year) as history,
                   sum(t.wins) as wins, sum(t.losses) as losses,
                   min(t.final_rank) as best_finish,
                   count(*) filter (where t.final_rank = 1) as titles
            from owners o
            left join team_season_stats t
              on t.owner_id = o.owner_id and t.final_rank is not null
             and t.season_year < %s
            where o.owner_id in (select owner_id from teams where season_year = %s)
            group by o.username
            order by o.username
        """, (season, season))]

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


def _missing(text, names):
    """Which of these names the prose never says."""
    lowered = text.lower()
    return [n for n in names if n.lower() not in lowered]


def missing_managers(text, facts):
    """Which managers the prose never names.

    Asking the prompt for full coverage gets most of the way; checking is
    what makes it true. The managers left out of a preview are the ones with
    the least to report, which is exactly who notices being left out.
    """
    return _missing(text, [t["manager"] for t in facts.get("teams", [])])


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
        # A scalar rather than rows: the season and the week are both single
        # numbers, and anything else added later will be too.
        if not isinstance(rows, list):
            out.append("%s: %s" % (key.replace("_", " ").title(), rows))
            continue
        if not rows:
            out.append("\n%s: none recorded" % key.replace("_", " ").title())
            continue
        out.append("\n%s:" % key.replace("_", " ").title())
        for r in rows:
            # A row that is only a note is already a sentence; prefixing it
            # with the word "note" puts a column name on every line of the
            # prompt and tells the reader nothing.
            if list(r) == ["note"]:
                out.append("  " + r["note"])
                continue
            out.append("  " + ", ".join(
                "%s %s" % (k, v) for k, v in r.items() if v is not None))
    return "\n".join(out)


def preview_prompt(facts):
    return """%s

Write the preview of the %s season.

There is a year behind this one and the facts carry it. Find what it says --
somebody climbing, somebody stuck, somebody who won it once and has not been
near it since, a best record that finished fourth -- and make that the claim
the piece is built on. Then the year ahead: who carries which titles into
it, the keeper decisions worth remarking on, how the choosing falls, the
rivalries with something already in them. Look forward, but do not report a
result that has not happened.

Every manager must be named somewhere in it. That is a floor, not a plan:
work them into sentences that are about something else. The ones with
nothing dramatic to report are the ones a lazy preview leaves out, and they
read it too -- but a piece that gives each of them a turn in order is a
directory, and the table underneath is already a better directory.

Use the season-by-season finishes. A league four years old has shapes in it --
someone climbing, someone stuck at the bottom, someone who won it once and has
not been near since -- and that is more interesting than last year alone.

A title is held by one manager at a time and rings their sigil wherever it is
drawn, so carrying one into a season means something. An earned crest is kept
for good. Both are worth a remark; neither is worth a paragraph.

The facts:
%s
""" % (VOICE, facts["season"], as_text(facts))


# ---- one week ----------------------------------------------------------


def _ordinal(n):
    """1 -> 1st. Used inside facts, so it has to agree with the site's own."""
    if n is None:
        return "?"
    if 10 <= n % 100 <= 20:
        return "%dth" % n
    return "%d%s" % (n, {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def _table_after(q, season, week):
    return q(standings.TABLE_AFTER_WEEK, {"y": season, "w": week})


def _runs(rows):
    """Everyone's current run, from a week-ordered list of results.

    Only runs of three or more come back. A two-game streak is a coincidence
    and naming it as a run is the sort of filler a reader sees through.
    """
    by_manager = {}
    for r in rows:
        by_manager.setdefault(r["username"], []).append(r["result"])
    out = []
    for name, results in sorted(by_manager.items()):
        if not results:
            continue
        last = results[-1]
        if last not in ("W", "L"):
            continue
        n = 0
        for res in reversed(results):
            if res != last:
                break
            n += 1
        if n >= 3:
            out.append({"note": "%s has %s %d in a row"
                                % (name, "won" if last == "W" else "lost", n)})
    return out


def _words(n):
    """Small numbers as words, because a fact that reads as a sentence is
    less likely to come back as a row."""
    return {2: "twice", 3: "three times", 4: "four times", 5: "five times",
            6: "six times", 7: "seven times"}.get(n, "%d times" % n)


def _season_so_far(q, season, week, runs_now):
    """What the weeks before this one say, without handing over the weeks.

    A week's account kept wanting things it could not see: that a crest had
    gone to the same sigil twice running, that a five-game run had just been
    broken, that a game was the closest of the season rather than of the
    afternoon. All of those are in the earlier weeks, and the earlier weeks
    are the last thing to put in a prompt -- thirteen weeks of results is a
    table, and a table in the facts comes back as a table in the prose.

    So the history arrives already reduced to the handful of sentences a
    writer actually reaches for. Tallies only where something has happened
    more than once, because "has taken it once" is not a pattern.
    """
    out = []

    # Who leads each weekly crest, and only where somebody does. Tallying
    # every manager who has taken one twice gave nineteen lines by week
    # fourteen, which is the table this whole approach exists to avoid.
    # Favoured by the Gods in particular goes to half the league every year,
    # so a five-way tie on three apiece is noise and is dropped; two names
    # sharing a lead is still a story and is kept.
    tallies = {}
    for r in q("""
        select o.username, cr.name, cr.sort_order, count(*) as n
        from owner_crests oc
        join crests cr on cr.crest_id = oc.crest_id
        join owners o on o.owner_id = oc.owner_id
        where oc.season_year = %(y)s and oc.week is not null
          and oc.week <= %(w)s and cr.standing = 'earned'
        group by o.username, cr.name, cr.sort_order
        having count(*) > 1
        order by cr.sort_order
    """, {"y": season, "w": week}):
        best, names = tallies.setdefault(r["name"], (0, []))
        if r["n"] > best:
            tallies[r["name"]] = (r["n"], [r["username"]])
        elif r["n"] == best:
            names.append(r["username"])
    for name, (n, who) in tallies.items():
        if len(who) > 2:
            continue
        out.append({"note": "%s %s taken %s %s this season, more than anyone"
                            % (" and ".join(who),
                               "has" if len(who) == 1 else "have",
                               name, _words(n))})

    # The marks of the season to date, so a week can be measured against the
    # year rather than only against itself.
    for r in q("""
        select username, week, round(points_for::numeric, 2) as pts
        from game_log
        where season_year = %(y)s and week <= %(w)s
        order by points_for desc limit 1
    """, {"y": season, "w": week}):
        out.append({"note": "the highest score of the season so far is %s's "
                            "%s in week %d" % (r["username"], r["pts"], r["week"])})
    for label, direction in (("widest", "desc"), ("narrowest", "asc")):
        for r in q("""
            select m.week, oa.username as a, ob.username as b,
                   round(abs(m.team_a_points - m.team_b_points)::numeric, 2) as gap
            from matchups m
            join teams ta on ta.team_id = m.team_a_id
            join owners oa on oa.owner_id = ta.owner_id
            join teams tb on tb.team_id = m.team_b_id
            join owners ob on ob.owner_id = tb.owner_id
            where m.season_year = %%(y)s and m.week <= %%(w)s
              and m.team_a_points is not null and m.team_b_points is not null
            order by abs(m.team_a_points - m.team_b_points) %s limit 1
        """ % direction, {"y": season, "w": week}):
            out.append({"note": "the %s margin of the season so far is %s, "
                                "%s and %s in week %d"
                                % (label, r["gap"], r["a"], r["b"], r["week"])})

    # A run of three or more that this week ended. The thing a weekly account
    # most wants to say and the one thing a single week cannot know.
    if week > 1:
        before = {r["note"].split(" has ")[0]: r["note"]
                  for r in _runs(q("""
                      select username, result from game_log
                      where season_year = %(y)s and game_type = 'regular'
                        and week <= %(w)s
                      order by week
                  """, {"y": season, "w": week - 1}))}
        still = {r["note"].split(" has ")[0] for r in runs_now}
        for name in sorted(set(before) - still):
            out.append({"note": "%s, and that run ended this week"
                                % before[name].replace(" has ", " had ", 1)})

    # Who has been at the head of the roll, and for how much of the season. A
    # leader three weeks in is a different story from a leader all year.
    #
    # One query with a running total rather than the table rebuilt once per
    # week. The obvious version called standings_after seventeen times for a
    # week seventeen summary, which is seventeen round trips to Neon for two
    # lines of output.
    leaders = {}
    for r in q("""
        with sides as (
            select m.week, m.team_a_id as team_id, m.team_a_points as pf,
                   (m.team_a_points > m.team_b_points)::int as won
            from matchups m
            where m.season_year = %(y)s and m.game_type = 'regular'
              and m.team_a_points is not null and m.team_b_points is not null
            union all
            select m.week, m.team_b_id, m.team_b_points,
                   (m.team_b_points > m.team_a_points)::int
            from matchups m
            where m.season_year = %(y)s and m.game_type = 'regular'
              and m.team_b_id is not null
              and m.team_a_points is not null and m.team_b_points is not null
        ), running as (
            select week, team_id,
                   sum(won) over (partition by team_id order by week) as wins,
                   sum(pf)  over (partition by team_id order by week) as pf
            from sides
        ), ranked as (
            select week, team_id,
                   row_number() over (partition by week
                                      order by wins desc, pf desc) as place
            from running where week <= %(w)s
        )
        select o.username
        from ranked r
        join teams t on t.team_id = r.team_id
        join owners o on o.owner_id = t.owner_id
        where r.place = 1
    """, {"y": season, "w": week}):
        leaders[r["username"]] = leaders.get(r["username"], 0) + 1
    played = sum(leaders.values())
    for name, n in sorted(leaders.items(), key=lambda kv: -kv[1]):
        if n > 1:
            out.append({"note": "%s has ended %d of the %d weeks played so "
                                "far at the head of the roll"
                                % (name, n, played)})
    return out


def week_facts(q, season, week):
    """What one week did, and what it leaves the league facing.

    A weekly summary is two jobs in one: the read on the week just played and
    the look at the week ahead. The facts are gathered in that order, and
    anything the model is allowed to call a superlative is stated as a
    sentence rather than left to be worked out from a sorted list -- the
    first preview turned a sorted points column into "led the league" and
    named the wrong manager.
    """
    facts = {"season": season, "week": week}

    games = q("""
        select m.game_type, m.team_a_points as a_points, m.team_b_points as b_points,
               m.team_a_projected as a_proj, m.team_b_projected as b_proj,
               oa.username as a, ta.team_name as a_team,
               ob.username as b, tb.team_name as b_team,
               exists (select 1 from rivalries r
                       where r.season_year = m.season_year
                         and r.owner_id = ta.owner_id
                         and r.rival_owner_id = tb.owner_id) as rival
        from matchups m
        join teams ta on ta.team_id = m.team_a_id
        join owners oa on oa.owner_id = ta.owner_id
        left join teams tb on tb.team_id = m.team_b_id
        left join owners ob on ob.owner_id = tb.owner_id
        where m.season_year = %(y)s and m.week = %(w)s
        order by m.matchup_id
    """, {"y": season, "w": week})

    # Written as sentences with the winner named. Handed two scores and two
    # names the model has to work out which is which, and it is the kind of
    # thing it gets right eleven times and wrong on the twelfth.
    played = [g for g in games if g["a_points"] is not None
              and g["b_points"] is not None]
    lines = []
    for g in games:
        label = "" if g["game_type"] == "regular" else \
            " (%s)" % g["game_type"].replace("_", " ")
        if g["b"] is None:
            lines.append({"note": "%s had a bye%s" % (g["a"], label)})
            continue
        if g["a_points"] is None or g["b_points"] is None:
            lines.append({"note": "%s plays %s%s, not played yet"
                                  % (g["a"], g["b"], label)})
            continue
        win, lose = ((g["a"], g["b"]) if g["a_points"] > g["b_points"]
                     else (g["b"], g["a"]))
        hi, lo = sorted([float(g["a_points"]), float(g["b_points"])], reverse=True)
        lines.append({"note": "%s beat %s %.2f to %.2f, by %.2f%s%s"
                              % (win, lose, hi, lo, hi - lo, label,
                                 ", and they are each other's rival"
                                 if g["rival"] else "")})
    facts["results"] = lines

    # The superlatives of the week, said in the words the model is allowed to
    # repeat. Nothing else in the facts licenses "highest" or "biggest".
    tops = []
    if played:
        sides = []
        for g in played:
            sides.append((float(g["a_points"]), g["a"], float(g["a_proj"] or 0), g["a_proj"]))
            sides.append((float(g["b_points"]), g["b"], float(g["b_proj"] or 0), g["b_proj"]))
        best = max(sides)
        worst = min(sides)
        tops.append({"note": "%s scored the most points of week %d, %.2f"
                             % (best[1], week, best[0])})
        tops.append({"note": "%s scored the fewest points of week %d, %.2f"
                             % (worst[1], week, worst[0])})
        # max/min with a key rather than sorting (margin, game) pairs: two
        # games with the same margin sent Python on to compare the dicts
        # beside them, which is a TypeError rather than a tie.
        def margin(g):
            return abs(float(g["a_points"]) - float(g["b_points"]))

        def won(g):
            return g["a"] if float(g["a_points"]) > float(g["b_points"]) else g["b"]

        def lost(g):
            return g["b"] if float(g["a_points"]) > float(g["b_points"]) else g["a"]

        for label, g in (("widest", max(played, key=margin)),
                         ("narrowest", min(played, key=margin))):
            tops.append({"note": "the %s margin of week %d was %.2f, %s over %s"
                                 % (label, week, margin(g), won(g), lost(g))})
        # Against the projection. The league keeps projections beside the
        # scores, so over- and under-performing is a fact here rather than a
        # guess -- and it is most of what makes a fantasy week feel unfair.
        over = [(pts - proj, name) for pts, name, proj, raw in sides if raw is not None]
        if over:
            up = max(over)
            down = min(over)
            tops.append({"note": "%s beat their projection by more than anyone "
                                 "in week %d, %.2f over" % (up[1], week, up[0])})
            tops.append({"note": "%s fell further below their projection than "
                                 "anyone in week %d, %.2f under"
                                 % (down[1], week, abs(down[0]))})
    facts["the_week_itself"] = tops

    # Where it leaves everyone, and who moved. A table on its own is a table;
    # the movement is the story in it.
    now = _table_after(q, season, week)
    facts["table_now"] = [
        {"note": "%s %s, %d-%d, %d points for"
                 % (_ordinal(i), r["username"], r["wins"], r["losses"],
                    round(float(r["points_for"])))}
        for i, r in enumerate(now, 1)]

    if week > 1:
        before = {r["username"]: i
                  for i, r in enumerate(_table_after(q, season, week - 1), 1)}
        moves = []
        for i, r in enumerate(now, 1):
            was = before.get(r["username"])
            if was and was != i:
                moves.append({"note": "%s %s from %s to %s"
                                      % (r["username"],
                                         "climbed" if i < was else "slipped",
                                         _ordinal(was), _ordinal(i))})
        facts["movement"] = moves

    facts["runs"] = _runs(q("""
        select username, result from game_log
        where season_year = %(y)s and game_type = 'regular' and week <= %(w)s
        order by week
    """, {"y": season, "w": week}))

    facts["the_season_so_far"] = _season_so_far(q, season, week, facts["runs"])

    facts["crests_settled_this_week"] = [
        {"note": "%s won %s%s" % (r["username"], r["name"],
                                  " -- %s" % r["detail"] if r["detail"] else "")}
        for r in q("""
            select cr.name, o.username, oc.detail
            from owner_crests oc
            join crests cr on cr.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            where oc.season_year = %(y)s and oc.week = %(w)s
              and cr.standing = 'earned'
            order by cr.sort_order
        """, {"y": season, "w": week})]

    # Who wore each title in this week, which is not the same as who wears it
    # now. The crest recompute writes a held snapshot for every week it
    # touches, so the answer is on record rather than inferred -- and without
    # reading it, an account of week 7 of 2023 would hand every title to
    # whoever holds it today. The live rows are the fallback for a week whose
    # snapshot never got written.
    held = q("""
        select cr.name, o.username
        from owner_crests oc
        join crests cr on cr.crest_id = oc.crest_id
        join owners o on o.owner_id = oc.owner_id
        where cr.standing = 'held' and oc.season_year = %(y)s and oc.week = %(w)s
        order by cr.sort_order
    """, {"y": season, "w": week}) or q("""
        select cr.name, o.username
        from owner_crests oc
        join crests cr on cr.crest_id = oc.crest_id
        join owners o on o.owner_id = oc.owner_id
        where cr.standing = 'held' and oc.season_year is null
        order by cr.sort_order
    """, {})
    facts["titles_held_that_week"] = [
        {"note": "%s holds %s" % (r["username"], r["name"])} for r in held]

    # Titles that moved this week. A week's snapshot says who wears each one;
    # only the week before it says whether that is news. Without this a
    # weekly account cannot report the one thing the league argues about most
    # -- and week 1 has no week before it, so it reports nothing, which is
    # correct rather than missing.
    facts["titles_that_changed_hands_this_week"] = [
        {"note": "%s took %s from %s this week"
                 % (r["taken_by"], r["name"], r["lost_by"])}
        for r in q("""
            with held as (
                select oc.week, cr.crest_id, cr.name, cr.sort_order, o.username
                from owner_crests oc
                join crests cr on cr.crest_id = oc.crest_id
                join owners o on o.owner_id = oc.owner_id
                where oc.season_year = %(y)s and cr.standing = 'held'
                  and oc.week in (%(w)s, %(w)s - 1)
            )
            select n.name, n.username as taken_by, b.username as lost_by
            from held n
            join held b on b.crest_id = n.crest_id and b.week = %(w)s - 1
            where n.week = %(w)s and n.username <> b.username
            order by n.sort_order
        """, {"y": season, "w": week})]

    # The week ahead, with enough beside each pairing to say something about
    # it: what each side has done so far, and what they have done to each
    # other across every season.
    record = {r["username"]: (r["wins"], r["losses"]) for r in now}
    ahead = []
    for g in q("""
        select m.game_type, oa.username as a, ob.username as b,
               exists (select 1 from rivalries r
                       where r.season_year = m.season_year
                         and r.owner_id = ta.owner_id
                         and r.rival_owner_id = tb.owner_id) as rival
        from matchups m
        join teams ta on ta.team_id = m.team_a_id
        join owners oa on oa.owner_id = ta.owner_id
        left join teams tb on tb.team_id = m.team_b_id
        left join owners ob on ob.owner_id = tb.owner_id
        where m.season_year = %(y)s and m.week = %(w)s
        order by m.matchup_id
    """, {"y": season, "w": week + 1}):
        if g["b"] is None:
            ahead.append({"note": "%s has a bye" % g["a"]})
            continue
        h2h = q("""
            select wins, losses from owner_head_to_head
            where username = %(a)s and opponent_username = %(b)s
        """, {"a": g["a"], "b": g["b"]})
        ra, rb = record.get(g["a"]), record.get(g["b"])
        note = "%s (%s) plays %s (%s)" % (
            g["a"], "%d-%d" % ra if ra else "no record yet",
            g["b"], "%d-%d" % rb if rb else "no record yet")
        if g["game_type"] != "regular":
            note += ", a %s" % g["game_type"].replace("_", " ")
        if h2h:
            # Whoever is actually ahead. The first version tested that
            # the records differed and then credited the lead to the
            # first name regardless, which produced "Niall leads it 0-2".
            won, lost = h2h[0]["wins"], h2h[0]["losses"]
            if won == lost:
                note += "; all time they are level at %d-%d" % (won, lost)
            elif won > lost:
                note += "; all time %s leads it %d-%d" % (g["a"], won, lost)
            else:
                note += "; all time %s leads it %d-%d" % (g["b"], lost, won)
        if g["rival"]:
            note += "; they are each other's rival"
        ahead.append({"note": note})
    facts["next_week"] = ahead

    # What the season has left, said from where this week sits rather than
    # from a count of regular weeks -- counting those alone told week 16 that
    # it was the last of the regular season.
    after = q("""
        select min(week) as next_week,
               count(distinct week) filter (where game_type = 'regular') as regular_left,
               count(distinct week) as weeks_left
        from matchups
        where season_year = %(y)s and week > %(w)s
    """, {"y": season, "w": week})
    a = after[0] if after else {"regular_left": 0, "weeks_left": 0, "next_week": None}
    if a["regular_left"]:
        note = ("%d regular season weeks remain after week %d"
                % (a["regular_left"], week))
    elif a["weeks_left"]:
        note = ("the regular season is over; the playoffs run from week %d"
                % a["next_week"])
    else:
        note = "week %d was the last week of the season" % week
    facts["what_is_left"] = [{"note": note}]
    return facts


# The closing paragraph exists only when there is a week after this one. Kept
# out of the template so both versions are wrapped the way the rest of the
# prompt is: a single unwrapped line in the middle of it reads, to anything
# scanning the prompt, like a different kind of instruction.
AHEAD = """Finish by looking at what comes next. Name the fixture worth
watching and say what is riding on it. Do not predict a winner as though it
has already happened."""

NO_AHEAD = """There is no week after this one to look ahead to, so end on
where the season stands."""


def week_prompt(facts):
    ahead = facts.get("next_week")
    return """%s

Write the league's account of week %s of %s: three paragraphs, or four if the
week earned it.

The first job is the week that was played. Not a run through every game --
the scores are on the same page and the reader has already looked at them.
Pick the two or three that mattered and say why, and let the rest be a clause
where they belong.

The second job is what it has done to the season. Who moved, who is running
out of weeks, who is quietly fine. A league table is only interesting as a
story about people trying to get up it.

The season so far is background, not material. It is there so a score can be
measured against the year rather than only against the afternoon, and so a
run that has just ended can be named as one. Reach for a line of it when it
earns its place. Do not work through it.

%s

The facts:
%s
""" % (VOICE, facts["week"], facts["season"],
       AHEAD if ahead else NO_AHEAD,
       as_text(facts))


def generate_week(q, season, week):
    """One week's account. No coverage retry: a week has six games and
    twelve managers, and insisting every one of them is named is what turns
    a report into a roll call."""
    facts = week_facts(q, season, week)
    return ask(week_prompt(facts)), []


# ---- a whole season ----------------------------------------------------


def season_facts(q, season):
    """What a finished year amounts to. Positions, not projections: by now
    everything is settled, and the only job left is to say what happened."""
    facts = {"season": season}

    facts["final_table"] = [
        {"note": "%s finished %s, %d-%d, %d points for and %d against"
                 % (r["username"], _ordinal(r["final_rank"]), r["wins"],
                    r["losses"], round(float(r["points_for"])),
                    round(float(r["points_against"])))}
        for r in q("""
            select username, final_rank, wins, losses, points_for, points_against
            from team_season_stats
            where season_year = %(y)s and final_rank is not null
            order by final_rank
        """, {"y": season})]

    facts["regular_season_table"] = [
        {"note": "%s %s on the regular season table, %d-%d, %d points for"
                 % (r["username"], _ordinal(i), r["wins"], r["losses"],
                    round(float(r["points_for"])))}
        for i, r in enumerate(q(standings.TABLE_AFTER_WEEK,
                                {"y": season, "w": 14}), 1)]

    # The bracket, game by game, in the order it was played. The path to a
    # title is the spine of a recap and it cannot be read off a final table.
    facts["the_playoffs"] = [
        {"note": "week %d %s: %s beat %s %.2f to %.2f"
                 % (r["week"], r["game_type"].replace("_", " "),
                    r["winner"], r["loser"], float(r["hi"]), float(r["lo"]))}
        for r in q("""
            select m.week, m.game_type,
                   case when m.team_a_points > m.team_b_points
                        then oa.username else ob.username end as winner,
                   case when m.team_a_points > m.team_b_points
                        then ob.username else oa.username end as loser,
                   greatest(m.team_a_points, m.team_b_points) as hi,
                   least(m.team_a_points, m.team_b_points) as lo
            from matchups m
            join teams ta on ta.team_id = m.team_a_id
            join owners oa on oa.owner_id = ta.owner_id
            join teams tb on tb.team_id = m.team_b_id
            join owners ob on ob.owner_id = tb.owner_id
            where m.season_year = %(y)s and m.game_type <> 'regular'
              and m.team_a_points is not null and m.team_b_points is not null
            order by m.week, m.game_type
        """, {"y": season})]

    facts["crests_won_this_season"] = [
        {"note": "%s won %s%s" % (r["username"], r["name"],
                                  " -- %s" % r["detail"] if r["detail"] else "")}
        for r in q("""
            select cr.name, o.username, oc.detail
            from owner_crests oc
            join crests cr on cr.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            where oc.season_year = %(y)s and cr.standing = 'earned'
              and oc.week is null
            order by cr.sort_order
        """, {"y": season})]

    facts["titles_at_the_close"] = [
        {"note": "%s ended the season holding %s" % (r["username"], r["name"])}
        for r in q("""
            select cr.name, o.username
            from owner_crests oc
            join crests cr on cr.crest_id = oc.crest_id
            join owners o on o.owner_id = oc.owner_id
            where oc.season_year = %(y)s and cr.standing = 'held'
              and oc.week is null
            order by cr.sort_order
        """, {"y": season})]

    # Stated superlatives again, for the same reason as everywhere else.
    tops = []
    for r in q("""
        select username, round(points_for) as pf from team_season_stats
        where season_year = %(y)s order by points_for desc limit 1
    """, {"y": season}):
        tops.append({"note": "%s scored the most points of the regular season, %d"
                             % (r["username"], r["pf"])})
    for r in q("""
        select username, round(points_against) as pa from team_season_stats
        where season_year = %(y)s order by points_against desc limit 1
    """, {"y": season}):
        tops.append({"note": "%s had the most points scored against them, %d"
                             % (r["username"], r["pa"])})
    for r in q("""
        select username, week, round(points_for::numeric, 2) as pf
        from game_log
        where season_year = %(y)s and game_type = 'regular'
        order by points_for desc limit 1
    """, {"y": season}):
        tops.append({"note": "the highest single week of the regular season was "
                             "%s's %s in week %d"
                             % (r["username"], r["pf"], r["week"])})
    facts["the_season_itself"] = tops

    # Where each of them came from. A recap that only knows this year cannot
    # say whose fourth-place finish is a collapse and whose is a triumph.
    facts["how_it_compares"] = [
        {"note": "%s finished %s this year, after %s"
                 % (r["username"], _ordinal(r["now"]),
                    r["before"] or "no earlier seasons")}
        for r in q("""
            select t.username, t.final_rank as now,
                   (select string_agg(p.season_year || ': ' || p.final_rank, ', '
                                      order by p.season_year)
                    from team_season_stats p
                    where p.owner_id = t.owner_id and p.final_rank is not null
                      and p.season_year < %(y)s) as before
            from team_season_stats t
            where t.season_year = %(y)s and t.final_rank is not null
            order by t.final_rank
        """, {"y": season})]

    # How the rivalries actually went. The pairings are in the preview facts
    # as names set against each other; a recap wants the result, because a
    # rivalry nobody won is a different story from one somebody swept.
    rivals = []
    for r in q("""
        with pairs as (
            select distinct least(r.owner_id, r.rival_owner_id) as a_id,
                            greatest(r.owner_id, r.rival_owner_id) as b_id
            from rivalries r where r.season_year = %(y)s
        )
        select oa.username as a, ob.username as b,
               count(g.matchup_id) as games,
               count(*) filter (where g.result = 'W') as a_wins,
               string_agg(g.game_type, ', ' order by g.week) as types
        from pairs p
        join owners oa on oa.owner_id = p.a_id
        join owners ob on ob.owner_id = p.b_id
        left join game_log g on g.season_year = %(y)s
             and g.owner_id = p.a_id and g.opponent_owner_id = p.b_id
        group by oa.username, ob.username
        order by oa.username
    """, {"y": season}):
        if not r["games"]:
            rivals.append({"note": "%s and %s were rivals and never met"
                                   % (r["a"], r["b"])})
            continue
        a_won, played = r["a_wins"], r["games"]
        every = {1: "it", 2: "both"}.get(played, "all %d" % played)
        if a_won == played:
            how = "%s won %s" % (r["a"], every)
        elif a_won == 0:
            how = "%s won %s" % (r["b"], every)
        else:
            how = "they split it %d-%d" % (a_won, played - a_won)
        extra = ""
        kinds = [t for t in (r["types"] or "").split(", ") if t != "regular"]
        if kinds:
            extra = ", one of them a %s" % kinds[0].replace("_", " ")
        rivals.append({"note": "%s and %s were rivals and met %s; %s%s"
                               % (r["a"], r["b"],
                                  {1: "once", 2: "twice"}.get(played,
                                                             "%d times" % played),
                                  how, extra)})
    facts["how_the_rivalries_went"] = rivals

    # Which titles changed hands over the year. A title is held by one
    # manager at a time and rings their sigil wherever it is drawn, so
    # losing one is a thing that happened to somebody. Read by comparing this
    # season's closing snapshot with the one before it; a title with no row
    # last year is one nobody had yet.
    facts["titles_that_changed_hands"] = [
        {"note": "%s took %s from %s" % (r["taken_by"], r["name"], r["lost_by"])
                 if r["lost_by"] else
                 "%s is the first to hold %s" % (r["taken_by"], r["name"])}
        for r in q("""
            with held as (
                select oc.season_year, cr.crest_id, cr.name, cr.sort_order,
                       o.username
                from owner_crests oc
                join crests cr on cr.crest_id = oc.crest_id
                join owners o on o.owner_id = oc.owner_id
                where oc.week is null and cr.standing = 'held'
                  and oc.season_year in (%(y)s, %(y)s - 1)
            )
            select n.name, n.username as taken_by, b.username as lost_by
            from held n
            left join held b on b.crest_id = n.crest_id
                            and b.season_year = %(y)s - 1
            where n.season_year = %(y)s
              and n.username is distinct from b.username
            order by n.sort_order
        """, {"y": season})]

    # One line per manager, not one per player. Thirty-five rows of manager,
    # player, round is a table, and a table in the facts comes back as a
    # table in the prose.
    facts["keepers_they_held"] = [
        {"note": "%s kept %s" % (r["manager"], r["kept"])}
        for r in q("""
            select o.username as manager,
                   string_agg(p.full_name || ' (R' || ks.cost_round || ')',
                              ', ' order by ks.cost_round) as kept
            from keeper_selections ks
            join teams t on t.team_id = ks.team_id
            join owners o on o.owner_id = t.owner_id
            join players p on p.player_id = ks.player_id
            where ks.season_year = %(y)s
            group by o.username
            order by o.username
        """, {"y": season})]
    return facts


def season_prompt(facts):
    return """%s

Write the account of the %s season: four or five paragraphs, looking back.

Start at the end -- who won it, and what it took. Then work outwards: the
regular season that set the bracket, the year somebody had that nobody saw
coming, the year somebody had that they would rather forget.

Every manager must be named somewhere in it, which is a floor and not a
plan. Twelve managers in a row with their finishes is the final table, and
the final table is directly underneath this.

A finish means more next to the ones before it. Fourth after three straight
last places is a different fourth from fourth after winning it.

The facts:
%s
""" % (VOICE, facts["season"], as_text(facts))


def generate_season(q, season):
    """The recap, with the same coverage check the preview gets: it is the
    one summary that is explicitly about all twelve of them."""
    facts = season_facts(q, season)
    prompt = season_prompt(facts)
    text = ask(prompt)
    missed = _missing(text, [r["username"] for r in q("""
        select username from team_season_stats where season_year = %(y)s
    """, {"y": season})])
    if missed:
        text = ask(prompt + """

Your previous attempt never mentioned %s. Write it again, covering the same
ground, and give each of them a reason to be there.

Previous attempt:
%s
""" % (", ".join(missed), text))
    return text, _missing(text, [r["username"] for r in q("""
        select username from team_season_stats where season_year = %(y)s
    """, {"y": season})])

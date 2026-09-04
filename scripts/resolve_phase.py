import os
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()

season = int(sys.argv[1])
phase = int(sys.argv[2])
dry = "--apply" not in sys.argv
url = os.environ.get("DATABASE_URL_DIRECT") or os.environ["DATABASE_URL"]


def first_free(want, taken):
    for r in range(want, 0, -1):
        if r not in taken:
            return r
    return None


with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            select opens_at, closes_at, resolved_at
            from keeper_windows where season_year = %s and phase = %s
        """, (season, phase))
        win = cur.fetchone()
        if not win:
            print(f"no window defined for {season} phase {phase}")
            raise SystemExit(1)
        if win[2]:
            print(f"{season} phase {phase} was already resolved at {win[2]}")
            raise SystemExit(1)
        if phase > 1:
            cur.execute("""
                select resolved_at from keeper_windows
                where season_year = %s and phase = %s
            """, (season, phase - 1))
            prev = cur.fetchone()
            if not prev or not prev[0]:
                print(f"phase {phase - 1} has not been resolved yet")
                raise SystemExit(1)

        cur.execute("""
            select distinct owner_id from keeper_eligibility where for_season = %s
        """, (season,))
        owners = [r[0] for r in cur.fetchall()]

        cur.execute("""
            select owner_id, phase, player_id, cost_round, origin, status
            from keeper_submissions where season_year = %s
        """, (season,))
        subs, taken = {}, {}
        for oid, ph, pid, rd, origin, status in cur.fetchall():
            subs[(oid, ph)] = (pid, origin, status)
            if rd and ph < phase:
                taken.setdefault(oid, set()).add(rd)

        cur.execute("""
            select owner_id, phase, player_id, contract_id, cost_round
            from keeper_phase_plan where season_year = %s
        """, (season,))
        plan = {(r[0], r[1]): r[2:] for r in cur.fetchall()}

        cur.execute("""
            select owner_id, player_id, term_years from keeper_plans
            where season_year = %s and phase = %s
        """, (season, phase))
        plans = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        cur.execute("""
            select k.owner_id, k.player_id, k.cost_round
            from keeper_eligibility k
            where k.for_season = %s and k.state in ('free', 'must_sign')
        """, (season,))
        costs = {(r[0], r[1]): r[2] for r in cur.fetchall()}

        cur.execute("select owner_id, username from owners")
        names = dict(cur.fetchall())
        cur.execute("select player_id, full_name from players")
        pnames = dict(cur.fetchall())

    actions = []
    for oid in sorted(owners, key=lambda o: names.get(o, "")):
        if (oid, phase) in subs:
            pid, origin, status = subs[(oid, phase)]
            actions.append((oid, "leave", None, None, None, None,
                            f"already submitted ({origin}, {status})"))
            continue

        entry = plan.get((oid, phase))
        if entry:
            pid, cid, want = entry
            held = taken.get(oid, set())
            rd = first_free(want, held)
            if rd is None:
                actions.append((oid, "error", pid, cid, None, None,
                                f"contract wants R{want} but no free round remains"))
                continue
            note = f"contract at R{rd}"
            if rd != want:
                note += f" (moved from R{want}, taken)"
            actions.append((oid, "auto", pid, cid, rd, None, note))
            continue

        planned = plans.get(oid)
        if planned and planned[0]:
            pid, term = planned
            want = costs.get((oid, pid))
            if want is None:
                actions.append((oid, "error", pid, None, None, None,
                                "planned player is not eligible"))
                continue
            held = taken.get(oid, set())
            rd = first_free(want, held)
            if rd is None:
                actions.append((oid, "error", pid, None, None, None,
                                f"planned player wants R{want}, no free round"))
                continue
            note = f"from plan at R{rd}"
            if rd != want:
                note += f" (moved from R{want}, taken)"
            actions.append((oid, "plan", pid, None, rd, term, note))
            continue

        actions.append((oid, "forfeit", None, None, None, None,
                        "no contract, no plan, nothing submitted"))

    print(f"{season} phase {phase} {'(dry run)' if dry else '(applying)'}\n")
    for oid, kind, pid, cid, rd, term, note in actions:
        who = names.get(oid, oid)
        what = pnames.get(pid, "") if pid else ""
        print(f"  {who:<10} {kind:<8} {what:<24} {note}")

    if any(a[1] == "error" for a in actions):
        print("\nerrors above, nothing written")
        raise SystemExit(1)

    if dry:
        print("\ndry run. re-run with --apply to write.")
        raise SystemExit(0)

    with conn.cursor() as cur:
        for oid, kind, pid, cid, rd, term, note in actions:
            if kind == "auto":
                cur.execute("""
                    insert into keeper_submissions
                        (season_year, phase, owner_id, player_id, cost_round,
                         contract_id, origin, status, note)
                    values (%s, %s, %s, %s, %s, %s, 'auto', 'approved', %s)
                """, (season, phase, oid, pid, rd, cid, note))
            elif kind == "plan":
                cur.execute("""
                    insert into keeper_submissions
                        (season_year, phase, owner_id, player_id, cost_round,
                         term_years, origin, status, note)
                    values (%s, %s, %s, %s, %s, %s, 'plan', 'pending', %s)
                """, (season, phase, oid, pid, rd, term, note))
            elif kind == "forfeit":
                cur.execute("""
                    insert into keeper_submissions
                        (season_year, phase, owner_id, origin, status, note)
                    values (%s, %s, %s, 'forfeit', 'approved', %s)
                """, (season, phase, oid, note))

        if phase == 1:
            cur.execute("""
                update keeper_voids set confirmed_at = now()
                where season_year = %s and confirmed_at is null
            """, (season,))

        cur.execute("""
            update keeper_windows set resolved_at = now(), updated_at = now()
            where season_year = %s and phase = %s
        """, (season, phase))
    conn.commit()

print(f"\nresolved {season} phase {phase}")

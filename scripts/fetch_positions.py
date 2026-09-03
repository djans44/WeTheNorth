import csv
import json
import pathlib
import re
import sys
import urllib.request

CACHE = pathlib.Path("data/sleeper_players.json")
POS_FILE = pathlib.Path("data/positions.csv")
WANTED = {"QB", "RB", "WR", "TE", "K", "DEF"}
SLOTS = {"QB", "WR", "RB", "TE", "W/T", "W/R/T", "Q/W/R/T", "DEF", "BN", "IR", "K"}
SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm(name):
    s = name.lower().replace(chr(8217), chr(39))
    s = re.sub(r"[.'`,]", "", s)
    s = SUFFIX.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(s):
    s = "".join(ch for ch in s if not (0xE000 <= ord(ch) <= 0xF8FF))
    return s.replace(chr(8217), chr(39)).strip()


if not CACHE.exists():
    print("downloading Sleeper player list (about 5 MB, one time)...")
    CACHE.parent.mkdir(exist_ok=True)
    with urllib.request.urlopen("https://api.sleeper.app/v1/players/nfl") as r:
        CACHE.write_bytes(r.read())
    print("cached to", CACHE)

data = json.loads(CACHE.read_text(encoding="utf-8"))
lookup = {}
for p in data.values():
    pos = p.get("position")
    if pos not in WANTED:
        continue
    if pos == "DEF":
        for key in (p.get("last_name"), p.get("full_name")):
            if key:
                lookup.setdefault(norm(key), "DEF")
        continue
    full = p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}"
    if full.strip():
        lookup.setdefault(norm(full), pos)
print(f"{len(lookup)} names loaded from Sleeper")


def names_in(path):
    lines = [clean(l) for l in path.read_text(encoding="utf-8").splitlines()]
    found, i = [], 0
    if any(re.match(r"^Round \d+$", l) for l in lines):
        for l in lines:
            m = re.match(r"^\d+\.\s*(.+)$", l)
            if m:
                parts = re.split(r"\t", m.group(1))
                if len(parts) < 2:
                    parts = re.split(r"\s{2,}", m.group(1))
                found.append(clean(parts[0]))
    else:
        while i < len(lines):
            if lines[i].strip() in SLOTS:
                i += 1
                while i < len(lines) and not lines[i].strip():
                    i += 1
                if i < len(lines):
                    found.append(lines[i].strip())
            i += 1
    return found


existing = {}
if POS_FILE.exists():
    for row in csv.DictReader(POS_FILE.open(encoding="utf-8-sig")):
        existing[row["player_name"]] = row["position"]

candidates, matched, missed = set(), 0, []
for arg in sys.argv[1:]:
    for n in names_in(pathlib.Path(arg)):
        candidates.add(n)

for n in sorted(candidates):
    if n in existing:
        continue
    pos = lookup.get(norm(n))
    if pos:
        existing[n] = pos
        matched += 1
    else:
        missed.append(n)

with POS_FILE.open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["player_name", "position"])
    for n in sorted(existing):
        w.writerow([n, existing[n]])

print(f"\n{len(candidates)} names in files, {matched} newly resolved, "
      f"{len(existing)} total in {POS_FILE}")
if missed:
    print(f"\n{len(missed)} could not be matched, add these by hand:")
    for m in missed:
        print(f"  {m},")

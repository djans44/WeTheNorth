"""Derive the web-sized manager portraits from the originals.

    python scripts/make_portraits.py

Reads `headshots/*.png` and writes `app/static/portraits/<username>.webp`.

The originals are 1086x1448 and about 2.5MB each, which is fine in git and
useless over Render's free tier. These are 640 wide -- twice the size they are
drawn at, so they stay sharp on a dense screen -- and land around a tenth of
the bytes.

Build-time only. Pillow is not in requirements.txt because the application
never opens an image: it serves files this script has already written.

The filenames are first names and the site keys off `owners.username`, which
agrees for eleven of the twelve. Dave is David.
"""
import os
import pathlib
import sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "headshots"
OUT = ROOT / "app" / "static" / "portraits"

WIDTH = 640
QUALITY = 82

# Anything not listed is lowercased as-is.
ALIASES = {"dave": "david"}


def main():
    if not SRC.is_dir():
        raise SystemExit("no headshots/ directory")
    OUT.mkdir(parents=True, exist_ok=True)

    files = sorted(SRC.glob("*.png"))
    if not files:
        raise SystemExit("no PNGs in headshots/")

    before = after = 0
    for path in files:
        stem = path.stem
        # BorysWeTheNorth.png -> borys
        name = stem[: -len("WeTheNorth")] if stem.endswith("WeTheNorth") else stem
        name = ALIASES.get(name.lower(), name.lower())

        img = Image.open(path).convert("RGB")
        w, h = img.size
        img = img.resize((WIDTH, round(h * WIDTH / w)), Image.LANCZOS)

        dest = OUT / ("%s.webp" % name)
        img.save(dest, "WEBP", quality=QUALITY, method=6)

        src_n, out_n = path.stat().st_size, dest.stat().st_size
        before += src_n
        after += out_n
        print("  %-26s -> %-14s %6.1fMB -> %5.0fKB  %dx%d"
              % (path.name, dest.name, src_n / 1048576, out_n / 1024, *img.size))

    print()
    print("  %d portraits, %.1fMB -> %.1fMB (%.0f%% smaller)"
          % (len(files), before / 1048576, after / 1048576,
             100 * (1 - after / before)))


if __name__ == "__main__":
    main()

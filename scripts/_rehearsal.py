"""Shared guard for scripts that write destructively.

The rehearsal runs against a Neon branch of production. Nothing here is clever
about which database is which -- it loads `.env.rehearsal` explicitly, loads
`.env` alongside it, and refuses if the two resolve to the same host. That way
the check needs no hard-coded hostname in git, and it fails closed: an
unconfigured `.env.rehearsal`, or one copy-pasted from production, stops the
script rather than pointing a delete at the real league.
"""
import os
import re

from dotenv import dotenv_values

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _host(url):
    m = re.search(r"@([^/]+)/", url or "")
    return m.group(1).replace("-pooler", "") if m else ""


def rehearsal_url(direct=True):
    """The rehearsal connection string, or exit with why not."""
    reh_path = os.path.join(ROOT, ".env.rehearsal")
    if not os.path.exists(reh_path):
        raise SystemExit(
            ".env.rehearsal does not exist. This script only ever runs against a\n"
            "branch, never against the database in .env.")

    reh = dotenv_values(reh_path)
    live = dotenv_values(os.path.join(ROOT, ".env"))

    url = reh.get("DATABASE_URL_DIRECT" if direct else "DATABASE_URL") or reh.get("DATABASE_URL")
    if not url:
        raise SystemExit(".env.rehearsal has no DATABASE_URL.")

    live_url = live.get("DATABASE_URL_DIRECT") or live.get("DATABASE_URL")
    if live_url and _host(url) and _host(url) == _host(live_url):
        raise SystemExit(
            "REFUSING: .env.rehearsal points at the same host as .env.\n"
            "  host: %s\n"
            "That is production. Create a Neon branch and use its connection\n"
            "string instead." % _host(url))

    return url


def describe(url):
    return _host(url) or "(unparsed host)"

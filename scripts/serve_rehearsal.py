"""Run the app against the rehearsal branch, never against production.

    python scripts/serve_rehearsal.py            # port 8100
    python scripts/serve_rehearsal.py 8101

The app calls load_dotenv(), which does not override variables already in the
environment, so setting them here wins over .env. Everything goes through the
same guard the teardown uses: if .env.rehearsal is missing, or resolves to the
same host as .env, this refuses rather than serving the real league on a port
labelled rehearsal.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts._rehearsal import rehearsal_url, describe, ROOT  # noqa: E402

from dotenv import dotenv_values  # noqa: E402


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8100

    # rehearsal_url() does the refusing; calling it first means a bad config
    # stops here rather than after the server is listening.
    direct = rehearsal_url(direct=True)
    reh = dotenv_values(os.path.join(ROOT, ".env.rehearsal"))

    os.environ["DATABASE_URL"] = reh.get("DATABASE_URL") or direct
    os.environ["DATABASE_URL_DIRECT"] = direct
    if reh.get("SECRET_KEY"):
        os.environ["SECRET_KEY"] = reh["SECRET_KEY"]
    # The summary generator needs this and it is not a database setting, so
    # it comes from the ordinary .env rather than the branch file.
    live = dotenv_values(os.path.join(ROOT, ".env"))
    if live.get("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = live["GEMINI_API_KEY"]

    print("serving the REHEARSAL branch")
    print("  host  %s" % describe(direct))
    print("  port  %s" % port)
    print()

    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()

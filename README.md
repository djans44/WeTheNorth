# WeTheNorth

Fantasy football league history site.

## Setup (Windows)
1. `python -m venv .venv`
2. `.\.venv\Scripts\Activate.ps1`
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and fill in DATABASE_URL
5. `uvicorn app.main:app --reload`

## Contributing
Branch off `main`, open a PR. Don't push to `main` directly.
Set git identity per-repo, not globally.

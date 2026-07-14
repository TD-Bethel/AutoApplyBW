# AutoApply BW — Project Configuration

## Project Overview
- **Name**: AutoApply BW — job-application assistant for Batswana / youth in Botswana
- **Tech Stack**: Python (Flask + Jinja2), waitress, fpdf2, pypdf. Data layer is
  dual-backend (app/db.py, no ORM): sqlite3 (stdlib) by default; PostgreSQL via
  pg8000 (pure Python) when DATABASE_URL is set — the latter lets the app run as
  many stateless instances behind a load balancer for scale/HA.
- **Free for everyone**: no payment or trial — any logged-in user gets the full
  app (feature_access() in app/auth.py is always true). The owner can still
  suspend/activate accounts via /admin. (Card-payment support — DPO Pay /
  Flutterwave hosted checkout — lives on the `add-card-payments` branch if it's
  ever wanted again.)
- **Deployment**: Fly.io or Render (Dockerfile + fly.toml / render.yaml)

## Architecture
@docs/architecture.md

## Hard Rules
- **Dependency-light, pure Python**: no packages that compile native wheels; prefer
  stdlib (the scraper uses urllib + xml.etree on purpose). Check requirements.txt
  before adding anything.
- **Offline-first with API fallback**: every AI feature must work without
  ANTHROPIC_API_KEY (rule-based fallback in app/services/ai.py). Never make the
  API a hard requirement — the owner avoids paid API usage.
- **Human-in-the-loop sending**: scraped/found jobs are drafts; a person confirms
  the application email and clicks send. Never auto-send.
- **Scraping politeness**: honour robots.txt, real User-Agent, timeouts, response
  caps, per-source caching (see app/services/scraper.py). One narrow exception:
  a source config may set `robots_exempt: True` for a *documented public API*
  whose docs invite programmatic use (robots.txt there targets search crawlers);
  cite the API docs in a comment. HTML scraping is never exempt. New job sources
  are config entries (_SF_SOURCES / _SCAN_SOURCES / _RSS_SOURCES / _JSON_SOURCES
  / _ATS_SOURCES), not new code paths.
- **Security invariants**: every state-changing form carries the CSRF token
  (`_csrf` + csrf_token()); user-owned rows are checked via _owned_app();
  SMTP values are CR/LF-stripped; secrets live in .env (never commit it).

## Code Style
- 4-space indentation, snake_case functions, UPPER_SNAKE module constants
- Private helpers prefixed `_`; route handlers thin, logic in app/services/
- Comments explain *why* (constraints, gotchas), not *what*
- SQL via parameterised query()/execute() helpers in app/db.py — never f-strings

## Common Commands
| Command | Purpose |
|---------|---------|
| `.venv\Scripts\python.exe run.py` | Dev server at http://127.0.0.1:8000 (Werkzeug, debug) |
| `.venv\Scripts\python.exe run.py --serve` | Production server (waitress) |
| `.venv\Scripts\python.exe smoke_test.py` | Fast end-to-end smoke tests (must stay ALL PASS) |
| `.venv\Scripts\python.exe integration_test.py` | Fuller integration suite |
| `.venv\Scripts\python.exe customer_journey.py` | Scripted user-journey walkthrough |

## Known Issues & Workarounds
- **Port 8000 ghost servers (Windows)**: Werkzeug's debug reloader leaves a child
  process bound to the port; a stale server then serves old code alongside the new
  one. Before debugging "stale behaviour", check
  `netstat -ano | findstr :8000` and kill *all* listed PIDs.
- **robots.txt fetching**: stdlib RobotFileParser.read() uses the Python-urllib
  User-Agent, which some WAFs 403 (e.g. Chobe). scraper.py fetches robots.txt
  manually with its own UA — keep it that way.
- **Job-source survey** (which SADC employers are scrapeable and why others
  aren't): see docs/job-sources.md before probing new sources.
- First-run admin comes from ADMIN_EMAIL / ADMIN_PASSWORD in .env.

---
**Last Updated**: 14 July 2026

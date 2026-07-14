<div align="center">

# 🇧🇼 AutoApply BW

**The free job-application assistant for Botswana's youth.**

Find real openings across Southern Africa and worldwide-remote boards, get your
CV scored and tailored to each advert by AI, and send personalised applications
from your own inbox — with a human confirming every send.

![Python](https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)
![Database](https://img.shields.io/badge/DB-SQLite%20%7C%20PostgreSQL-336791?logo=postgresql&logoColor=white)
![Dependencies](https://img.shields.io/badge/dependencies-pure%20Python-brightgreen)
![Access](https://img.shields.io/badge/access-free%20for%20everyone-blue)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/TD-Bethel/AutoApplyBW)

</div>

---

## ✨ What's inside

| Page | What you get |
|---|---|
| **Dashboard** | Upload a CV → instant score + concrete fixes; add jobs; tailor; send — the whole pipeline on one page |
| **Jobs** | Live vacancies scraped per country (🇧🇼 🇳🇦 🇿🇲 🇿🇦 🇿🇼) + an *apply-directly* directory of employers we link to instead of scraping |
| **Remote Jobs** | Work-from-anywhere roles from five free worldwide boards + ATS boards (Greenhouse/Lever) |
| **Career Team** | Three isolated AI assistants — CV coach, cover-letter writer, interview prep — each with its own memory |
| **Support chat** | Users reach the owner in-app; scripted auto-replies handle the common questions |
| **Admin** | Owner panel: user list, CV viewer, support inbox, suspend/activate as a moderation backstop |

**Free for everyone** — no payments, no trial. Anyone who registers gets the
full app. (A complete card-payment/subscription build is preserved on the
`add-card-payments` branch if it's ever wanted.)

## 🧠 The AI is offline-first

Every AI feature works **without any API key** — a rule-based engine scores,
tailors and drafts using keyword matching and templates. Set
`ANTHROPIC_API_KEY` (or any OpenAI-compatible endpoint via `DEEPSEEK_API_URL`)
and the same features are AI-powered instead — the output shape is identical,
so nothing else changes. The owner provides **one** key for all users.

## 🌍 Where the jobs come from

- **Botswana** — Mascom, BTC, BPC, Chobe Holdings, Botswana Stock Exchange
- **Namibia** — Telecom Namibia, NamPost
- **South Africa** — Capitec, Discovery, Nedbank, Luno
- **Zimbabwe** — Delta Corporation
- **Remote (worldwide)** — Remotive, RemoteOK, WeWorkRemotely, WorkingNomads,
  Jobicy, plus Greenhouse ATS boards (Canonical, GitLab, Remote.com)

The scraper is stdlib-only, robots-respecting, cached, parallel, and
failure-tolerant; **new sources are one-line config entries**, not code. Full
details + the SADC employer survey: [docs/job-sources.md](docs/job-sources.md).

## 🚀 Quick start

```bash
git clone https://github.com/TD-Bethel/AutoApplyBW.git && cd AutoApplyBW
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
#   edit .env: set SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD
python run.py                     # dev server → http://127.0.0.1:8000
python run.py --serve             # production server (waitress)
```

First run creates the **admin account** from `ADMIN_EMAIL` / `ADMIN_PASSWORD`.

## ☁️ Deploy — built to scale

The **Deploy to Render** button above reads [`render.yaml`](render.yaml), which
provisions the launch architecture:

```
users ──► Render load balancer ──► 2× stateless app instances ──► managed PostgreSQL
                                       │  /healthz (checks the DB, pulls a dead
                                       │   instance out of rotation)
                                       └─ HSTS · secure cookies · Sentry · JSON logs
```

- **SQLite by default, PostgreSQL when `DATABASE_URL` is set** — same code,
  auto-detected ([app/db.py](app/db.py), pure-Python `pg8000` driver). Postgres
  is what allows multiple instances; add more with `numInstances`.
- Sessions are signed cookies (stateless) — no sticky sessions needed; just keep
  `SECRET_KEY` fixed and shared.
- Optional [Sentry](https://sentry.io) error tracking (`SENTRY_DSN`) and
  structured JSON request logs (`LOG_JSON=1`).
- Fly.io works too: [`fly.toml`](fly.toml) + [`Dockerfile`](Dockerfile).

## ⚙️ Configuration

All via `.env` (see [.env.example](.env.example) for the full annotated list):

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Session signing — **required in production**, must be identical across instances |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | First-run owner account |
| `DATABASE_URL` | Set to a `postgres://…` URL to switch to PostgreSQL (else SQLite at `DATABASE_PATH`) |
| `COOKIE_SECURE=1` | Secure cookies + HSTS when serving HTTPS |
| `ANTHROPIC_API_KEY` | Optional — AI-powered tailoring (blank = free rule-based engine) |
| `SMTP_HOST/PORT/USER/PASSWORD` | Platform mailbox for password resets (users configure their own sending inbox in Settings) |
| `SEND_RATE_PER_HOUR` | Per-user hourly send cap (anti-spam, default 30) |
| `SENTRY_DSN`, `LOG_JSON` | Observability (both optional) |

## 🧪 Tests

| Command | What it covers |
|---|---|
| `python smoke_test.py` | Fast end-to-end suite — auth, CSRF, pages, admin, throttling (**must stay ALL PASS**) |
| `python integration_test.py` | Fuller integration suite |
| `python customer_journey.py` | Scripted walk-through of a real user's journey |

## 🗂️ Project layout

```
AutoApplyBW/
├── run.py                     # entry point (dev / --serve)
├── render.yaml  fly.toml  Dockerfile
├── smoke_test.py  integration_test.py  customer_journey.py
├── docs/
│   ├── architecture.md        # how the parts connect (mermaid diagram)
│   └── job-sources.md         # source adapters + SADC employer survey
└── app/
    ├── __init__.py            # app factory, config, security headers, first-run admin
    ├── db.py                  # SQLite/PostgreSQL dual-backend data layer (no ORM)
    ├── security.py            # CSRF, login throttling, rate limits, safe redirects
    ├── observability.py       # structured request logs + optional Sentry
    ├── auth.py                # register / login / access decorators
    ├── main.py                # dashboard, CV, jobs, remote jobs, sending, settings
    ├── admin.py               # owner panel
    ├── assistants.py          # Career Team (AI assistants)
    ├── support.py             # in-app support chat
    ├── services/
    │   ├── scraper.py         # job discovery (all source adapters)
    │   ├── ai.py              # review + tailoring (API or offline rules)
    │   ├── cv_parser.py       # PDF / DOCX / TXT → text
    │   ├── pdf.py             # tailored CV → PDF (fpdf2)
    │   └── emailer.py         # per-user SMTP sending
    ├── templates/             # Jinja2 pages
    └── static/                # styles, JS, images
```

## 🔒 Security

- **CSRF tokens** on every state-changing form (per-session, no extra deps)
- **Login throttled** (8 fails / 15 min / IP+email) with constant-time dummy-hash
  checks; **register + password-reset requests rate-limited** against bots
- Suspension kills **existing sessions immediately**
- `HttpOnly` + `SameSite` cookies; `Secure` + **HSTS** under HTTPS
- CSP, `X-Frame-Options`, `nosniff`, referrer-policy headers on every response
- SMTP values **CR/LF-stripped** (no header injection); recipients validated
- Password-reset tokens stored **hashed**, short-lived, single-use
- User data lives in the DB (git-ignored locally); never commit your `.env`

## 🙌 Credits

- Remote-board adapters ported from [GigPilot](https://github.com/TD-Bethel/GigPilot)
- The one-adapter-per-ATS idea from [career-ops](https://github.com/santifer/career-ops) (MIT)
- Background photos: Diego Delso (CC BY-SA 4.0), Temptious (CC0), via Wikimedia Commons

## 🗺️ Roadmap

- Application tracking (replies, interview status)
- Background worker for AI + email (keeps request threads free as traffic grows)
- More job sources — see the [survey](docs/job-sources.md) for candidates

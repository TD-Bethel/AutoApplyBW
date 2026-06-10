# AutoApply BW

An automated job-application assistant for Batswana / youth in Botswana.

Users can:

- **Upload and check their CV** — get a score and concrete, actionable suggestions.
- **Tailor their CV and cover letter to each job** — keyword-matched to the advert
  (AI-powered with Claude when an API key is set, with a free rule-based fallback).
- **Apply to many companies** — personalised emails sent from the user's own inbox,
  rate-limited to protect against spam blacklisting.

Access is **owner-controlled**: users register, pay offline (Orange Money / MyZaka /
bank transfer), and the owner activates their account from an admin panel.

---

## Tech stack

Pure-Python and dependency-light so it installs and runs on any modern Python
(including 3.14) without compiling native wheels:

- **Flask** — web framework (server-rendered Jinja2 templates)
- **sqlite3** (stdlib) — database, no ORM
- **requests** — calls the Claude API directly (no heavy SDK)
- **pypdf** — read text from PDF CVs (DOCX is parsed with the stdlib)
- **waitress** — production WSGI server

---

## Quick start

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell:  .venv\Scripts\Activate.ps1
# source .venv/bin/activate     # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
copy .env.example .env          # cp .env.example .env  on macOS/Linux
#   then edit .env and set SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD,
#   and (optionally) ANTHROPIC_API_KEY

# 4. Run
python run.py                   # dev server at http://127.0.0.1:8000
python run.py --serve           # production server (waitress)
```

On first run an **admin account** is created from `ADMIN_EMAIL` / `ADMIN_PASSWORD`
in your `.env`. Log in with it to reach the **Admin** panel and activate users.

---

## How it works

### Access control (the paywall)
1. A user signs up → account is `pending`.
2. They pay you offline and message you.
3. You open **Admin**, set them to **Active** (optionally with an expiry date for a
   subscription). Only active, non-expired users can tailor and send applications.

### CV tailoring
- With `ANTHROPIC_API_KEY` set, Claude reviews the CV and rewrites the CV + cover
  letter to match each job description — **without inventing facts**.
- Without a key, a free rule-based engine matches job keywords and fills a
  professional cover-letter template.
- You (the owner) provide **one** API key for everyone, so users never need their own.

### Email sending
- Each user configures **their own** email (SMTP) in **Settings** — applications are
  sent from their inbox, which is far better for deliverability than blasting from one
  shared account.
- Sending is **rate-limited per hour** (`SEND_RATE_PER_HOUR`) to avoid spam blocks.
- For Gmail/Outlook users must use an **app password**, not their normal password.

---

## Project layout

```
AutoApplyBW/
├── run.py                  # entry point
├── requirements.txt
├── .env.example
├── app/
│   ├── __init__.py         # app factory, config, first-run admin
│   ├── db.py               # sqlite3 data layer + schema
│   ├── security.py         # CSRF, login throttling, safe redirects
│   ├── auth.py             # register/login + access-control decorators
│   ├── main.py             # dashboard, CV, applications, settings
│   ├── admin.py            # owner panel: activate / suspend users
│   ├── services/
│   │   ├── cv_parser.py    # PDF / DOCX / TXT -> text
│   │   ├── ai.py           # Claude review + tailoring (rule-based fallback)
│   │   └── emailer.py      # SMTP sending
│   ├── templates/          # Jinja2 pages
│   └── static/style.css
└── instance/               # database + data (git-ignored, never committed)
```

---

## Security & privacy notes

- All state-changing forms are **CSRF-protected** (per-session token, no extra deps).
- Login is **rate-limited** (8 failed attempts per 15 minutes per IP+email) and uses a
  constant-time dummy-hash check so attackers can't probe which emails exist.
- Suspending a user kills their **existing session immediately**, not just new logins.
- Session cookies are `HttpOnly` + `SameSite=Lax`; set `COOKIE_SECURE=1` in production
  (HTTPS) so they are `Secure` too. Sessions expire after 14 days.
- Responses carry security headers (CSP, `X-Frame-Options`, `nosniff`, referrer policy).
- Email sending strips CR/LF from user-supplied values (no SMTP header injection) and
  validates recipient addresses.
- User data (the SQLite DB, including CVs) lives in `instance/` and is **git-ignored**.
- Email app-passwords are stored in the database and are never echoed back into HTML.
  For production, run on a host you control and consider encrypting these at rest.
- Never commit your real `.env`.

---

## Roadmap

- Automated online payments (DPO Pay / Flutterwave / mobile money) instead of manual activation
- A scraper / directory of Botswana company contacts
- PDF export of the tailored CV (instead of plain-text attachment)
- Application tracking (replies, interview status)

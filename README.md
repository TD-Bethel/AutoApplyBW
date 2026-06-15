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

## Find jobs (built-in job search)

The dashboard's **Find jobs** card searches live company vacancy pages across
Southern Africa (with a country picker: Botswana, Namibia, Zambia, South Africa)
and turns results into one-click application drafts.

**Botswana**
- **Mascom** — `mascom.bw/vacancy-listings/` (HTML vacancy table; expired adverts filtered out)
- **BTC** — `btc.bw` job openings (WordPress JSON API, no HTML scraping)
- **BPC** — `careers.bpc.bw` (SAP SuccessFactors)
- **Chobe Holdings** — `chobeholdings.co.bw` (vacancy links, safari camps/lodges)
- **Botswana Stock Exchange** — `bse.co.bw/vacancies/` (vacancy notice PDFs)

**Namibia**
- **Telecom Namibia** — `telecom.na/vacancies` (vacancy notice PDFs)
- **NamPost** — `nampost.com.na/corporate/vacancies` (vacancy headings)

**South Africa**
- **Capitec Bank** — `careers.capitecbank.co.za` (SAP SuccessFactors)
- **Discovery** — `careers.discovery.co.za` (SAP SuccessFactors)
- **Nedbank** — `jobs.nedbank.co.za` (SAP SuccessFactors)

**Zimbabwe**
- **Delta Corporation** — `delta.co.zw/vacancies/` (vacancy notice PDFs)

The scraper (`app/services/scraper.py`) is stdlib-only, honours `robots.txt`,
caches each source for 10 minutes, fetches sources in parallel, interleaves
sources fairly in unranked results, and never breaks the dashboard if a source
is down. Results are reviewed by the user — the application email is confirmed
by a human before anything is sent. New sources are **config-only**:
SuccessFactors portals go in `_SF_SOURCES`, simple careers pages (vacancy
links, PDFs, or headings) in `_SCAN_SOURCES`, RSS/Atom feeds in `_RSS_SOURCES`.
To discover more sources manually, this search works well:
`(site:.co.bw OR site:.com.na OR site:.co.zm OR site:.co.za) ("careers" OR "vacancies")`.

### Source survey (June 2026)

Other employers checked, for future adapters:

- **Careers page exists, no openings right now** (recheck periodically; some may
  work with `_SCAN_SOURCES` once their listing markup is visible): Bomaid, CEDA,
  LEA, BOCRA, BoFiNet, Letshego, Air Botswana, Botswana Railways, Bank of
  Botswana, BIHL, Minet, Debswana, First Capital, Choppies
  (`jobs.choppies.co.bw`), Cresta Marakanelo, Bank Gaborone, Namdeb,
  Debmarine, Namibia Wildlife Resorts, Sefalana.
- **Behind an ATS / JS app that can't be scraped simply**: FNB (Workday),
  Stanbic / Standard Chartered / Access Bank (group-level portals), Botswana
  Life + NamWater + Woolworths + Isuzu (eRecruit/Trending Talent), Botswana Oil
  + Pep (MCI Direct Hire), De Beers (global list, no per-job location), BHC +
  WUC + ZESCO (JavaScript apps without a discoverable public API), Eskom
  (custom portal), Sasol (SuccessFactors but renders jobs via JS), Shoprite +
  Clicks + Spar + Ford + Truworths + Raubex + Aveng + Oceana (group portals/ATS
  widgets), BMW + DSV + Investec + First Quantum (JS-rendered job lists).
- **Site unreachable at survey time**: Orange BW, Khoemacau (broken SSL), BDC
  (broken SSL), Engen, TotalEnergies BW, Turnstar, BotswanaPost, MTC Namibia,
  Namib Mills, Zamtel, O&L Group, Zambia Sugar, CEC, NICO, Far Property,
  RDC Properties, Courier Guy, Tiger Brands — plus, from the June 2026 SADC
  sweep: most ZW/ZM/NA corporate sites (Namdeb, Rössing, NamPower, Namport,
  Gondwana, TransNamib, ZCCM, KCM, Zanaco-careers, Econet, NetOne, OK Zimbabwe,
  Meikles, Innscor, NRZ and others) refuse connections from non-browser
  clients or are simply down.
- **Careers info page but no machine-readable listings**: Zanaco, ZACL,
  Zambeef, Mr Price, TFG, Sanlam, BITC, FSG, Minergy, Tlou Energy, NamPower
  (recruitment portal has broken SSL).
- **Facebook groups**: cannot be used — posts sit behind a login wall and
  Facebook's terms prohibit automated collection.
- **Automating the `site:` search formula**: search engines block automated
  queries (and their terms prohibit it), so source discovery stays manual —
  but each find is a one-line config entry.

---

## Roadmap

- Automated online payments (DPO Pay / Flutterwave / mobile money) instead of manual activation
- More job sources (Khoemacau and Orange BW careers pages were unreachable at build time; recheck)
- PDF export of the tailored CV (instead of plain-text attachment)
- Application tracking (replies, interview status)

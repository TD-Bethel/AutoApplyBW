# Job sources — how they work & survey results

The scraper ([`app/services/scraper.py`](../app/services/scraper.py)) is
stdlib-only, honours `robots.txt`, caches each source for 10 minutes, fetches
sources in parallel, interleaves sources fairly in unranked results, and never
breaks a page when a source is down. Results are always reviewed by a human —
the application email is confirmed before anything is sent.

**Adding a source is config-only** — pick the adapter that matches the site:

| Adapter | Config list | Fits |
|---|---|---|
| SuccessFactors | `_SF_SOURCES` | SAP careers portals (`careers.<company>…/search/`) |
| Link/heading scan | `_SCAN_SOURCES` | Simple careers pages (vacancy links, PDFs, headings) |
| RSS / Atom | `_RSS_SOURCES` | Any job feed |
| JSON board | `_JSON_SOURCES` | Remote boards with JSON APIs |
| ATS board | `_ATS_SOURCES` | Any company on Greenhouse / Lever (public JSON APIs) |
| Apply-directly | `EMPLOYER_DIRECTORY` | JS-rendered portals we must not/can't scrape — listed as links |

To discover more sources manually, this search works well:
`(site:.co.bw OR site:.com.na OR site:.co.zm OR site:.co.za) ("careers" OR "vacancies")`.
(Search engines block automated queries, so discovery stays manual — but each
find is a one-line config entry.)

## Working sources

**Botswana** — Mascom (HTML vacancy table, expired adverts filtered), BTC
(WordPress JSON API), BPC (SuccessFactors), Chobe Holdings (vacancy links),
Botswana Stock Exchange (vacancy-notice PDFs).

**Namibia** — Telecom Namibia (vacancy PDFs), NamPost (vacancy headings).

**South Africa** — Capitec, Discovery, Nedbank (SuccessFactors), Luno
(Greenhouse ATS).

**Zimbabwe** — Delta Corporation (vacancy-notice PDFs).

**Remote (worldwide)** — RemoteOK, WorkingNomads (JSON APIs), WeWorkRemotely
(RSS), Remotive and Jobicy (JSON APIs, `robots_exempt`: their robots.txt
disallow `/api` but both publish API docs inviting programmatic use — policy in
CLAUDE.md), plus Greenhouse ATS boards for Canonical, GitLab and Remote.com.
Remote listings have **no application email** — applications go through the
board's own page. Boards ported from the
[GigPilot](https://github.com/TD-Bethel/GigPilot) project; the one-adapter-per-ATS
idea comes from [career-ops](https://github.com/santifer/career-ops) (MIT).

## Source survey (June 2026)

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
  The JS-rendered ones that matter to users (e.g. Access Bank, Bank of Baroda)
  are surfaced on the Jobs page via `EMPLOYER_DIRECTORY` as apply-directly links.
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

## Remote-board / ATS survey (July 2026)

- **Working**: RemoteOK, WorkingNomads, WeWorkRemotely, Remotive, Jobicy;
  Greenhouse boards for Luno (ZA), Canonical, GitLab, Remote.com.
- **Retired/moved ATS slugs** (API returns 404 — these employers likely changed
  ATS; re-survey before re-adding): Andela, Flutterwave, Yoco (Greenhouse),
  Paystack (Lever).

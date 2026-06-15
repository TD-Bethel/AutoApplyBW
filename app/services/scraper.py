"""Job discovery ("Find jobs").

Searches one or more job sources for adverts matching the user's keywords and
returns normalised listings the user can review and add as applications.

Design goals (consistent with the rest of the app):
  * **Pure stdlib** — no BeautifulSoup/feedparser. RSS/Atom is parsed with
    xml.etree; HTTP uses urllib. So no new wheels to compile.
  * **Offline-first** — a built-in `sample` source always works (and is used in
    tests / demos), so the feature is usable before any real source is wired up.
  * **Polite** — every network source declares a URL, we honour robots.txt,
    send a real User-Agent, time out fast, and cap the response size.

A "source" is just an entry in SOURCES. To add a real Botswana job board, add a
config dict with its RSS/Atom feed URL (see _RSS_SOURCES) — no code changes.
"""
from __future__ import annotations

import html as html_mod
import json
import re
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

USER_AGENT = "AutoApplyBW/1.0 (+job-search; respects robots.txt)"
HTTP_TIMEOUT = 10          # seconds — fail fast, this runs in a web request
MAX_BYTES = 2_000_000      # cap a single feed download (~2 MB)
MAX_PER_SOURCE = 25        # don't flood the UI from one feed
MAX_RESULTS = 40           # total cap returned to the page
CACHE_TTL = 600            # cache each source's results for 10 min (be polite)


# --------------------------------------------------------------------------- model
COUNTRIES = {"BW": "Botswana", "NA": "Namibia", "ZM": "Zambia", "ZA": "South Africa",
             "ZW": "Zimbabwe"}


def _listing(title, company="", email="", description="", url="", location="",
             source="", posted="", country="BW"):
    """A normalised job advert. Keys mirror the `applications` columns so a
    listing can be dropped straight into the Add-job form."""
    return {
        "job_title": _clean(title),
        "company_name": _clean(company),
        "company_email": _clean(email),
        "job_description": _clean(description, limit=4000),
        "url": url.strip(),
        "location": _clean(location),
        "source": source,
        "posted": posted,
        "country": country,
    }


def _clean(text, limit=300):
    text = re.sub(r"<[^>]+>", " ", text or "")          # strip any stray HTML
    text = html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


_EMAIL_RE = re.compile(r"[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}")

_MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august september october november december".split()
)}
_CLOSING_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})")


def _closed(closing_text):
    """True if a closing date like '8th June 2026' is in the past.

    Unparseable or missing dates return False — keep the listing and let the
    user judge from the advert.
    """
    m = _CLOSING_RE.search(closing_text or "")
    if not m:
        return False
    month = _MONTHS.get(m.group(2).lower())
    if not month:
        return False
    try:
        closing = date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return False
    return closing < date.today()


def _find_email(*texts):
    """Best-effort: pull an application email out of a description if present."""
    for t in texts:
        m = _EMAIL_RE.search(t or "")
        if m:
            return m.group(0)
    return ""


# --------------------------------------------------------------------------- HTTP
_ROBOTS_CACHE = {}  # netloc -> (RobotFileParser | None, fetched_at)


def _can_fetch(url):
    """Respect robots.txt (cached per host). On failure, default to NOT fetching.

    We fetch robots.txt ourselves (instead of RobotFileParser.read()) because
    the stdlib fetch uses the default Python-urllib User-Agent, which some site
    firewalls reject — and a rejected fetch would wrongly read as "deny all".
    """
    try:
        parts = urlparse(url)
        cached = _ROBOTS_CACHE.get(parts.netloc)
        if cached and time.time() - cached[1] < 3600:
            rp = cached[0]
        else:
            rp = urllib.robotparser.RobotFileParser()
            robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
            try:
                req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
                    body = resp.read(200_000).decode("utf-8", errors="replace")
                rp.parse(body.splitlines())
            except urllib.error.HTTPError as exc:
                # RFC 9309: a 4xx robots.txt means "no restrictions" (some WAFs
                # 403 bot UAs on robots.txt while happily serving the pages —
                # e.g. delta.co.zw). 5xx falls through to the outer except and
                # is treated as deny, the conservative reading.
                if 500 <= exc.code:
                    return False
                rp.allow_all = True
            _ROBOTS_CACHE[parts.netloc] = (rp, time.time())
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return False


def _http_get(url):
    if not _can_fetch(url):
        raise PermissionError(f"robots.txt disallows {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
        return resp.read(MAX_BYTES)


def _http_text(url):
    return _http_get(url).decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- RSS source
def _fetch_rss(cfg):
    """Fetch and parse one RSS/Atom feed into listings.

    cfg = {"name", "feed", "company"?}. Returns [] on any error so one broken
    source never breaks the whole search.
    """
    try:
        raw = _http_get(cfg["feed"])
        root = ET.fromstring(raw)
    except Exception:
        return []

    listings = []
    # RSS <item> and Atom <entry> both carry title/link/description.
    items = root.iter("item")
    items = list(items) or [e for e in root.iter() if e.tag.endswith("entry")]
    for item in items[:MAX_PER_SOURCE]:
        title = _child_text(item, "title")
        desc = _child_text(item, "description") or _child_text(item, "summary")
        link = _child_text(item, "link") or _atom_link(item)
        if not title:
            continue
        listings.append(_listing(
            title=title,
            company=cfg.get("company", ""),
            email=_find_email(desc),
            description=desc,
            url=link,
            source=cfg["name"],
            posted=_child_text(item, "pubDate") or _child_text(item, "updated"),
        ))
    return listings


def _child_text(item, tag):
    for child in item:
        if child.tag == tag or child.tag.endswith("}" + tag):
            return (child.text or "").strip()
    return ""


def _atom_link(item):
    for child in item:
        if child.tag.endswith("link") and child.get("href"):
            return child.get("href")
    return ""


# --------------------------------------------------------------------------- sample source (offline)
# A small, realistic set of Botswana-style adverts. This makes "Find jobs" work
# end-to-end with no network and serves as the always-available fallback. These
# are illustrative placeholders, not real vacancies.
_SAMPLE = [
    _listing("Graduate Trainee — Finance", "Botswana Insurance Holdings",
             "careers@bih.co.bw",
             "Seeking BGCSE/degree graduates in finance or accounting. Strong "
             "Excel, communication and analytical skills. Gaborone based.",
             location="Gaborone", source="sample"),
    _listing("Junior Software Developer", "Brastorne Enterprises",
             "jobs@brastorne.com",
             "Python or JavaScript experience, eager to learn, build mobile and "
             "web products for rural communities. Internship to permanent.",
             location="Gaborone", source="sample"),
    _listing("Administrative Assistant", "Debswana",
             "recruitment@debswana.bw",
             "Office administration, record keeping, MS Office, customer service. "
             "Diploma in business administration an advantage.",
             location="Jwaneng", source="sample"),
    _listing("Sales Representative", "Choppies Distribution",
             "hr@choppies.co.bw",
             "Driving licence, retail or FMCG sales experience, target driven, "
             "good communication in Setswana and English.",
             location="Francistown", source="sample"),
    _listing("Registered Nurse", "Bokamoso Private Hospital",
             "vacancies@bokamoso.co.bw",
             "Registered with the Nursing and Midwifery Council of Botswana, "
             "minimum two years clinical experience, shift work.",
             location="Mmopane", source="sample"),
    _listing("Primary School Teacher", "Northside Primary School",
             "admin@northside.ac.bw",
             "Diploma or degree in primary education, registered with BEC, "
             "passionate about early childhood learning.",
             location="Gaborone", source="sample"),
]


def _fetch_sample(cfg):
    return list(_SAMPLE)


# --------------------------------------------------------------------------- Mascom
# https://mascom.bw/vacancy-listings/ renders a plain <table class="vacancy_table">
# with columns: Job Title (link) | Location | Division | Closing Date.
def _fetch_mascom(cfg):
    try:
        page = _http_text("https://mascom.bw/vacancy-listings/")
    except Exception:
        return []
    listings = []
    for row in re.findall(r"<tr>(.*?)</tr>", page, re.S)[:MAX_PER_SOURCE + 5]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 4:
            continue  # header row or unrelated table
        link = re.search(r'href="([^"]+)"', cells[0])
        title = _clean(cells[0])
        if not title:
            continue
        location, division, closing = _clean(cells[1]), _clean(cells[2]), _clean(cells[3])
        if _closed(closing):
            continue  # advert has already expired — don't waste an application
        listings.append(_listing(
            title=title,
            company="Mascom Wireless",
            description=(f"Division: {division}. Location: {location}. "
                         f"Closing date: {closing}. Open the advert for full "
                         f"requirements and how to apply."),
            url=link.group(1).strip() if link else "https://mascom.bw/vacancy-listings/",
            location=location,
            source="mascom",
            posted=closing,
        ))
    return listings[:MAX_PER_SOURCE]


# --------------------------------------------------------------------------- BTCL
# btc.bw runs WordPress with the wp-job-openings plugin, which exposes adverts
# as JSON via the standard REST API — no HTML parsing needed.
def _fetch_btcl(cfg):
    url = "https://btc.bw/company/wp-json/wp/v2/awsm_job_openings?per_page=25"
    try:
        data = json.loads(_http_text(url))
    except Exception:
        return []
    listings = []
    for post in data[:MAX_PER_SOURCE]:
        title = _clean(post.get("title", {}).get("rendered", ""))
        if not title:
            continue
        desc = post.get("content", {}).get("rendered", "")
        listings.append(_listing(
            title=title,
            company="BTC (Botswana Telecommunications Corporation)",
            email=_find_email(desc),
            description=desc,
            url=post.get("link", ""),
            location="Botswana",
            source="btcl",
            posted=_clean(post.get("date", ""))[:10],
        ))
    return listings


# --------------------------------------------------------------------------- SuccessFactors
# Many SADC corporates run SAP SuccessFactors career sites (careers.<company>.*).
# The /search/ results page lists jobs as table rows with /job/... links.
# Config-only: {"name", "company", "base", "country", "location"?}.
def _fetch_successfactors(cfg):
    base = cfg["base"].rstrip("/")
    try:
        page = _http_text(base + "/search/")
    except Exception:
        return []
    listings = []
    rows = re.findall(r'<tr[^>]*class="[^"]*data-row[^"]*"[^>]*>(.*?)</tr>', page, re.S)
    for row in rows[:MAX_PER_SOURCE]:
        a = re.search(r'href="(/job/[^"]+)"[^>]*>(.*?)</a>', row, re.S)
        if not a:
            continue
        loc = re.search(r'class="[^"]*jobLocation[^"]*"[^>]*>(.*?)</', row, re.S)
        location = _clean(loc.group(1)) if loc else cfg.get("location", "")
        listings.append(_listing(
            title=_clean(a.group(2)),
            company=cfg["company"],
            description=(f"Location: {location}. Open the advert for full "
                         f"requirements (applications go through the company's careers portal)."),
            url=base + a.group(1),
            location=location,
            source=cfg["name"],
            country=cfg.get("country", "BW"),
        ))
    return listings


_SF_SOURCES = [
    {"name": "bpc", "company": "Botswana Power Corporation",
     "base": "https://careers.bpc.bw", "country": "BW", "location": "Botswana"},
    {"name": "capitec", "company": "Capitec Bank",
     "base": "https://careers.capitecbank.co.za", "country": "ZA", "location": "South Africa"},
    {"name": "discovery", "company": "Discovery Limited",
     "base": "https://careers.discovery.co.za", "country": "ZA", "location": "South Africa"},
    {"name": "nedbank", "company": "Nedbank",
     "base": "https://jobs.nedbank.co.za", "country": "ZA", "location": "South Africa"},
]


# --------------------------------------------------------------------------- generic link-scan
# Many employers list vacancies as simple links (or headings) on a careers page.
# Config-only. Link mode: give the page, a href pattern for advert links, and
# how to derive the title ("slug" from the URL, or the link "text"). Headings
# mode ({"mode": "headings"}): job titles are <hN> headings after a section
# marker (section_re) — the listing links back to the careers page itself.
def _fetch_scan(cfg):
    try:
        page = _http_text(cfg["page"])
    except Exception:
        return []
    if cfg.get("mode") == "headings":
        return _scan_headings(cfg, page)
    listings, seen = [], set()
    pattern = r'<a[^>]+href="(' + cfg["href_re"] + r')"[^>]*>(.*?)</a>'
    for m in re.finditer(pattern, page, re.S | re.I):
        url, text = urljoin(cfg["page"], m.group(1)), _clean(m.group(2))
        if url in seen:
            continue
        seen.add(url)
        if cfg.get("title") == "slug":
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            title = re.sub(r"\.\w+$", "", slug).replace("-", " ").replace("_", " ")
            title = re.sub(r"\s+", " ", title).strip().title()
        else:
            title = text
        if not title:
            continue
        # Optional sanity filter so nav/footer links don't masquerade as jobs.
        if cfg.get("title_re") and not re.search(cfg["title_re"], title, re.I):
            continue
        listings.append(_scan_listing(cfg, title, url))
    return listings[:MAX_PER_SOURCE]


def _scan_headings(cfg, page):
    m = re.search(cfg["section_re"], page, re.I)
    if not m:
        return []
    seg = page[m.end():]
    stop = re.search(r"<h2[\s>]", seg, re.I)
    if stop:
        seg = seg[:stop.start()]
    tag = cfg.get("heading_tag", "h3")
    listings = []
    for raw in re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", seg, re.S | re.I)[:MAX_PER_SOURCE]:
        title = _clean(raw)
        if not title or len(title) > 90:
            continue
        listings.append(_scan_listing(cfg, title, cfg["page"]))
    return listings


def _scan_listing(cfg, title, url):
    return _listing(
        title=title,
        company=cfg["company"],
        description=(f"Vacancy at {cfg['company']}. Open the advert for full "
                     f"requirements and how to apply."),
        url=url,
        location=cfg.get("location", "Botswana"),
        source=cfg["name"],
        country=cfg.get("country", "BW"),
    )


# Job-title words used to tell vacancy adverts apart from other documents/links.
_JOB_WORDS = (r"vacanc|specialist|analyst|officer|manager|coordinator|assistant|"
              r"executive|engineer|accountant|technician|supervisor|clerk|graduate|"
              r"intern|director|consultant|administrator|attendant|guide|chef|"
              r"housekeeper|waiter|driver|operator|teacher|nurse|auditor|apprentice|"
              r"sales")

_SCAN_SOURCES = [
    {
        "name": "chobe",
        "company": "Chobe Holdings",
        "country": "BW",
        "page": "https://www.chobeholdings.co.bw/about-us/careers/",
        "href_re": r"https://www\.chobeholdings\.co\.bw/vacancy/[^\"]+",
        "title": "slug",
        "location": "Botswana (safari camps/lodges)",
    },
    {
        "name": "bse",
        "company": "Botswana Stock Exchange",
        "country": "BW",
        "page": "https://www.bse.co.bw/vacancies/",
        "href_re": r"https://www\.bse\.co\.bw/wp-content/uploads/[^\"]+\.pdf",
        "title": "text",
        "title_re": _JOB_WORDS,
        "location": "Gaborone",
    },
    {
        "name": "telecom_na",
        "company": "Telecom Namibia",
        "country": "NA",
        "page": "https://www.telecom.na/vacancies",
        "href_re": r"/images/company/vacancies/[^\"]+\.pdf",
        "title": "slug",
        "location": "Namibia",
    },
    {
        "name": "nampost",
        "company": "NamPost",
        "country": "NA",
        "page": "https://www.nampost.com.na/corporate/vacancies",
        "mode": "headings",
        "section_re": r">\s*Current\s+Vacancies\s*<",
        "heading_tag": "h3",
        "location": "Namibia",
    },
    {
        # Same pattern as BSE: vacancy notice PDFs with the job title as link text.
        "name": "delta_zw",
        "company": "Delta Corporation",
        "country": "ZW",
        "page": "https://delta.co.zw/vacancies/",
        "href_re": r"https://delta\.co\.zw/wp-content/uploads/[^\"]+\.pdf",
        "title": "text",
        "title_re": _JOB_WORDS,
        "location": "Zimbabwe",
    },
]


# --------------------------------------------------------------------------- sources registry
# `fetch` is a function taking the cfg dict. To add an RSS/Atom job feed, append
# {"name": ..., "feed": ...} to _RSS_SOURCES — no code changes needed.
_RSS_SOURCES = [
    # e.g. {"name": "Jobs Botswana", "feed": "https://www.example.co.bw/jobs/feed/"},
]

SOURCES = {
    "mascom": {"name": "mascom", "country": "BW", "fetch": _fetch_mascom},
    "btcl": {"name": "btcl", "country": "BW", "fetch": _fetch_btcl},
    "sample": {"name": "sample", "country": "BW", "fetch": _fetch_sample},
}
for _cfg in _SF_SOURCES:
    SOURCES[_cfg["name"]] = {**_cfg, "fetch": _fetch_successfactors}
for _cfg in _SCAN_SOURCES:
    SOURCES[_cfg["name"]] = {**_cfg, "fetch": _fetch_scan}
for _cfg in _RSS_SOURCES:
    SOURCES[_cfg["name"]] = {**_cfg, "fetch": _fetch_rss}

# The sample source is for demos/tests; exclude it from live searches by default.
DEFAULT_SOURCES = [n for n in SOURCES if n != "sample"]


def source_names():
    return list(SOURCES)


# --------------------------------------------------------------------------- cache
_CACHE = {}  # source name -> (listings, fetched_at)
_CACHE_LOCKS = defaultdict(threading.Lock)  # per-source: no fetch stampedes


def _cached_fetch(name, src):
    hit = _CACHE.get(name)
    if hit and time.time() - hit[1] < CACHE_TTL:
        return hit[0]
    # One thread refetches a stale source; concurrent searches wait briefly
    # and reuse its result instead of all hitting the site at once.
    with _CACHE_LOCKS[name]:
        hit = _CACHE.get(name)
        if hit and time.time() - hit[1] < CACHE_TTL:
            return hit[0]
        listings = src["fetch"](src)
        # Empty is a legitimate state (no current openings) — cache it too, so
        # we don't hammer a source on every search.
        _CACHE[name] = (listings, time.time())
        return listings


# Background refresher: after the first search, re-fetch sources just before
# the cache expires so user searches are always served from a warm cache and
# never hold a web request hostage for the ~8s a cold fetch takes.
_REFRESH_STARTED = False
_REFRESH_GUARD = threading.Lock()


def _ensure_refresher():
    global _REFRESH_STARTED
    if _REFRESH_STARTED:
        return
    with _REFRESH_GUARD:
        if _REFRESH_STARTED:
            return
        threading.Thread(target=_refresh_loop, daemon=True,
                         name="scraper-cache-refresh").start()
        _REFRESH_STARTED = True


def _refresh_loop():
    while True:
        time.sleep(CACHE_TTL * 0.9)
        for name in DEFAULT_SOURCES:
            try:
                _CACHE[name] = (SOURCES[name]["fetch"](SOURCES[name]), time.time())
            except Exception:
                pass  # keep serving the stale entry; retry next cycle


_WARM_LOCK = threading.Lock()


def warm_async():
    """Fetch all sources on a background thread if the cache is cold.

    Called from page views that want vacancy data without ever blocking the
    request — the first view kicks this off and renders what's cached (maybe
    nothing); the next view finds the cache warm.
    """
    if not _WARM_LOCK.acquire(blocking=False):
        return  # a warm-up is already running

    def _runner():
        try:
            with ThreadPoolExecutor(max_workers=min(8, len(DEFAULT_SOURCES)) or 1) as pool:
                for n in DEFAULT_SOURCES:
                    pool.submit(_cached_fetch, n, SOURCES[n])
        finally:
            _WARM_LOCK.release()

    threading.Thread(target=_runner, daemon=True, name="scraper-warm").start()
    _ensure_refresher()


def overview(per_country=4):
    """Most recent cached listings grouped by country code — cache only, never
    fetches. Sources are interleaved within a country so one busy employer
    doesn't crowd out the others."""
    queues_by_country = {}
    for name in DEFAULT_SOURCES:
        hit = _CACHE.get(name)
        if not hit or not hit[0]:
            continue
        country = SOURCES[name].get("country", "BW")
        queues_by_country.setdefault(country, []).append(list(hit[0]))
    out = {}
    for country, queues in queues_by_country.items():
        picked = []
        while queues and len(picked) < per_country:
            for q in queues[:]:
                if len(picked) >= per_country:
                    break
                picked.append(q.pop(0))
                if not q:
                    queues.remove(q)
        out[country] = picked
    return out


# --------------------------------------------------------------------------- search
def search(keywords="", location="", sources=None, limit=MAX_RESULTS, country=""):
    """Search the enabled sources and return ranked, de-duplicated listings.

    keywords/location are simple, case-insensitive substring filters; country is
    an ISO-ish code from COUNTRIES ("" = all). Ranking is by how many query terms
    appear in the title+description (most relevant first).
    """
    _ensure_refresher()
    terms = [t for t in re.split(r"[\s,]+", keywords.lower()) if t]
    loc = location.lower().strip()
    names = [n for n in (sources or DEFAULT_SOURCES) if n in SOURCES]
    if country:
        names = [n for n in names if SOURCES[n].get("country", "BW") == country]

    # Fetch sources in parallel — each is slow network I/O and independent.
    collected = []
    with ThreadPoolExecutor(max_workers=min(8, len(names)) or 1) as pool:
        futures = [pool.submit(_cached_fetch, n, SOURCES[n]) for n in names]
        for fut in futures:
            try:
                collected.extend(fut.result())
            except Exception:
                continue  # never let one source break the whole search

    seen, results = set(), []
    for item in collected:
        item = dict(item)  # never mutate cached/shared listings (score below)
        hay = f"{item['job_title']} {item['job_description']} {item['company_name']}".lower()
        if terms and not any(t in hay for t in terms):
            continue
        if loc and loc not in f"{item['location'].lower()} {hay}":
            continue
        key = (item["job_title"].lower(), item["company_name"].lower())
        if key in seen:
            continue
        seen.add(key)
        item["score"] = sum(hay.count(t) for t in terms) if terms else 0
        results.append(item)

    if terms:
        results.sort(key=lambda r: r["score"], reverse=True)
    else:
        # No ranking signal: interleave sources round-robin so one big employer
        # can't crowd everyone else out of the result cap.
        by_src = {}
        for item in results:
            by_src.setdefault(item["source"], []).append(item)
        queues, results = list(by_src.values()), []
        while queues:
            for q in queues[:]:
                results.append(q.pop(0))
                if not q:
                    queues.remove(q)
    return results[:limit]

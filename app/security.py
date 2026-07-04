"""Security helpers: CSRF protection, login throttling, safe redirects.

Dependency-free (stdlib + Flask only) on purpose, matching the rest of the app.
"""
import re
import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from flask import session, request, abort

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(value):
    return bool(value) and len(value) <= 254 and bool(EMAIL_RE.match(value))


# ------------------------------------------------------------------ CSRF
# Endpoints that authenticate by other means (e.g. a provider webhook proving
# itself with a signed header) and therefore must NOT be CSRF-checked.
_CSRF_EXEMPT = set()


def csrf_exempt(endpoint):
    """Register a view endpoint (e.g. "billing.webhook") as CSRF-exempt."""
    _CSRF_EXEMPT.add(endpoint)


def csrf_token():
    """Return (creating if needed) the per-session CSRF token."""
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def csrf_protect():
    """Reject state-changing requests without a valid CSRF token."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if request.endpoint in _CSRF_EXEMPT:
        return
    expected = session.get("_csrf", "")
    received = request.form.get("_csrf", "") or request.headers.get("X-CSRF-Token", "")
    if not expected or not received or not secrets.compare_digest(expected, received):
        abort(400, "Invalid or missing CSRF token. Refresh the page and try again.")


# ------------------------------------------------------------------ login throttle
# Sliding-window limiter kept in memory. Single-process deployments (waitress)
# are exactly what this app targets, so this is sufficient and dependency-free.
_attempts = defaultdict(deque)
_attempts_lock = Lock()

MAX_ATTEMPTS = 8          # failures allowed per window
WINDOW_SECONDS = 15 * 60  # 15 minutes


def _client_key(email):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?")
    ip = ip.split(",")[0].strip()
    return f"{ip}|{email}"


def too_many_attempts(email):
    """True if this IP+email has exceeded the failed-login budget."""
    key = _client_key(email)
    now = time.monotonic()
    with _attempts_lock:
        window = _attempts[key]
        while window and now - window[0] > WINDOW_SECONDS:
            window.popleft()
        return len(window) >= MAX_ATTEMPTS


def record_failed_attempt(email):
    with _attempts_lock:
        _attempts[_client_key(email)].append(time.monotonic())


def clear_attempts(email):
    with _attempts_lock:
        _attempts.pop(_client_key(email), None)


def rate_limited(bucket, limit, window=WINDOW_SECONDS):
    """Generic per-client-IP sliding-window limit for non-login endpoints
    (e.g. registration, password-reset requests). Records the hit and returns
    True once the client exceeds `limit` hits in `window` seconds.

    Note: in-memory, so per-instance when scaled out — an attacker's hits spread
    across instances. Good enough to blunt bots/email-bombing; move to a shared
    store (Postgres/Redis) if you need a hard global cap."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    key = f"{bucket}|{ip}"
    now = time.monotonic()
    with _attempts_lock:
        hits = _attempts[key]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            return True
        hits.append(now)
        return False


# ------------------------------------------------------------------ redirects
def safe_next(target):
    """Only allow same-site relative redirect targets (blocks open redirects)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None

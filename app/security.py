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


# ------------------------------------------------------------------ redirects
def safe_next(target):
    """Only allow same-site relative redirect targets (blocks open redirects)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None

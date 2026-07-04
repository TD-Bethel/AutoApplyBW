"""Production observability: structured request logs + optional error tracking.

Two pieces, both zero-cost when unconfigured (offline-first rule):

  - Request logging: one structured line per request — method, path, status,
    duration in ms, and the user id when logged in. With LOG_JSON=1 the line is
    a JSON object (what log aggregators want); otherwise it stays human-readable
    for local dev. Static files and the load-balancer's /healthz probes are
    skipped so the log stays signal, not noise.

  - Sentry: activated ONLY when SENTRY_DSN is set. Import is lazy, so the
    sentry-sdk package need not even be installed to run without it. PII is not
    sent (send_default_pii=False) — Sentry sees stack traces, not user emails.
"""
import json
import logging
import time

from flask import g, request


def setup(app):
    _setup_sentry(app)
    _setup_request_logging(app)


def _setup_sentry(app):
    dsn = (app.config.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            send_default_pii=False,          # never ship user emails/IPs to Sentry
            traces_sample_rate=0.05,         # light performance sampling
            environment="production" if app.config["SESSION_COOKIE_SECURE"] else "dev",
        )
        app.logger.info("Sentry error tracking enabled.")
    except Exception as exc:  # noqa: BLE001 — observability must never kill the app
        app.logger.warning("Sentry init failed (continuing without it): %s", exc)


# Paths that would drown the log in noise while telling us nothing.
_SKIP_PREFIXES = ("/static/",)
_SKIP_PATHS = ("/healthz", "/favicon.ico")


def _setup_request_logging(app):
    log = logging.getLogger("autoapply.request")
    as_json = app.config.get("LOG_JSON")

    @app.before_request
    def _start_timer():
        g._req_start = time.perf_counter()

    @app.after_request
    def _log_request(resp):
        path = request.path
        if path in _SKIP_PATHS or path.startswith(_SKIP_PREFIXES):
            return resp
        dur_ms = round((time.perf_counter() - g.get("_req_start", time.perf_counter())) * 1000, 1)
        user = g.user["id"] if getattr(g, "user", None) else None
        if as_json:
            log.info(json.dumps({
                "method": request.method, "path": path, "status": resp.status_code,
                "ms": dur_ms, "user": user,
            }))
        else:
            log.info("%s %s -> %s (%sms)%s", request.method, path,
                     resp.status_code, dur_ms, f" user={user}" if user else "")
        return resp

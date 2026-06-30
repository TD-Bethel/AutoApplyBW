"""Core user-facing routes: dashboard, CV, job applications, settings."""
from datetime import datetime, timezone, timedelta
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, g, current_app,
    abort, Response
)

from .auth import login_required, active_required, access_active, feature_access, trial_state
from .db import query, execute, now_iso
from .security import valid_email
from .services import cv_parser, ai, pdf, scraper
from .services.emailer import send_application, EmailError

MAX_CV_CHARS = 200_000      # caps pathological uploads (e.g. zip-bombed DOCX)
MAX_JOB_DESC_CHARS = 30_000
MAX_FIELD = 200

bp = Blueprint("main", __name__)


@bp.app_context_processor
def _inject_countries():
    # The Find-jobs form (dashboard) always needs the country list.
    return {"countries": scraper.COUNTRIES}


@bp.route("/")
def index():
    if g.user:
        return redirect(url_for("main.dashboard"))
    return render_template("index.html")


def _dashboard_context(**extra):
    """Shared template context for every route that renders dashboard.html —
    one place to keep them in sync."""
    ctx = dict(
        apps=query(
            "SELECT * FROM applications WHERE user_id = ? ORDER BY created_at DESC",
            (g.user["id"],),
        ),
        # Trial users can use features too, so buttons stay enabled for them.
        is_active=feature_access(g.user),
        trial=trial_state(g.user),
        sent_last_hour=_sent_last_hour(g.user["id"]),
        rate_limit=current_app.config["SEND_RATE_PER_HOUR"],
        # Cache-only read: never blocks the page. warm_async() fills it.
        vacancy_overview=scraper.overview(),
    )
    ctx.update(extra)
    return ctx


@bp.route("/dashboard")
@login_required
def dashboard():
    scraper.warm_async()  # fill the vacancy cache in the background
    return render_template("dashboard.html", **_dashboard_context())


@bp.route("/jobs")
@login_required
def jobs():
    """Dedicated Jobs page: browse currently-available vacancies and search.
    Browsing is open to any logged-in user; running a search is a full-access
    feature (paid or in-trial), matching the dashboard's Find-jobs gating."""
    scraper.warm_async()  # fill the vacancy cache in the background
    keywords = request.args.get("keywords", "").strip()[:MAX_FIELD]
    location = request.args.get("location", "").strip()[:MAX_FIELD]
    country = request.args.get("country", "").strip()
    if country not in scraper.COUNTRIES:
        country = ""  # "" = all countries
    found = None  # None = no search run yet; [] = searched, nothing matched
    if (keywords or location or country) and feature_access(g.user):
        try:
            found = scraper.search(keywords=keywords, location=location, country=country)
        except Exception:  # noqa: BLE001 — search must never 500 the page
            current_app.logger.exception("Job search failed")
            found = []
            flash("Job search is unavailable right now. Please try again later.", "warning")
        if not found and keywords:
            flash("No matching jobs found. Try fewer or different keywords.", "info")
    return render_template(
        "jobs.html",
        vacancy_overview=scraper.overview(per_country=12),
        found=found,
        find_keywords=keywords,
        find_location=location,
        find_country=country,
        is_active=feature_access(g.user),
    )


# ------------------------------------------------------------------ CV upload + review
@bp.route("/cv/upload", methods=["POST"])
@active_required
def cv_upload():
    file = request.files.get("cv_file")
    if not file or not file.filename:
        flash("Please choose a CV file to upload.", "danger")
        return redirect(url_for("main.dashboard"))
    try:
        text = cv_parser.extract_text(file.filename, file.read())
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("main.dashboard"))
    execute(
        "UPDATE users SET cv_text = ?, cv_filename = ?, cv_uploads = cv_uploads + 1 "
        "WHERE id = ?",
        (text[:MAX_CV_CHARS], file.filename[:MAX_FIELD], g.user["id"]),
    )
    flash("CV uploaded and read successfully.", "success")
    return redirect(url_for("main.dashboard"))


@bp.route("/cv/review", methods=["POST"])
@active_required
def cv_review():
    user = query("SELECT * FROM users WHERE id = ?", (g.user["id"],), one=True)
    result = ai.review_cv(user["cv_text"])
    return render_template("dashboard.html", **_dashboard_context(review=result))


# ------------------------------------------------------------------ job applications
@bp.route("/jobs/add", methods=["POST"])
@active_required
def job_add():
    company = request.form.get("company_name", "").strip()[:MAX_FIELD]
    email = request.form.get("company_email", "").strip()[:MAX_FIELD]
    title = request.form.get("job_title", "").strip()[:MAX_FIELD]
    desc = request.form.get("job_description", "").strip()[:MAX_JOB_DESC_CHARS]
    if not valid_email(email):
        flash("A valid company email is required.", "danger")
        return redirect(url_for("main.dashboard"))
    execute(
        """INSERT INTO applications
           (user_id, company_name, company_email, job_title, job_description, status, created_at)
           VALUES (?, ?, ?, ?, ?, 'draft', ?)""",
        (g.user["id"], company, email, title, desc, now_iso()),
    )
    flash(f"Added application for {company or email}.", "success")
    return redirect(url_for("main.dashboard"))


@bp.route("/jobs/find", methods=["POST"])
@active_required
def job_find():
    keywords = request.form.get("keywords", "").strip()[:MAX_FIELD]
    location = request.form.get("location", "").strip()[:MAX_FIELD]
    country = request.form.get("country", "").strip()
    if country not in scraper.COUNTRIES:
        country = ""  # "" = all countries
    try:
        listings = scraper.search(keywords=keywords, location=location, country=country)
    except Exception:  # noqa: BLE001 — search must never 500 the dashboard
        current_app.logger.exception("Job search failed")
        listings = []
        flash("Job search is unavailable right now. Please try again later.", "warning")
    if not listings and keywords:
        flash("No matching jobs found. Try fewer or different keywords.", "info")
    return render_template("dashboard.html", **_dashboard_context(
        found=listings,
        find_keywords=keywords,
        find_location=location,
        find_country=country,
    ))


def _owned_app(app_id):
    app = query("SELECT * FROM applications WHERE id = ?", (app_id,), one=True)
    if app is None or app["user_id"] != g.user["id"]:
        abort(404)
    return app


@bp.route("/jobs/<int:app_id>/tailor", methods=["POST"])
@active_required
def job_tailor(app_id):
    app = _owned_app(app_id)
    user = query("SELECT * FROM users WHERE id = ?", (g.user["id"],), one=True)
    if not user["cv_text"].strip():
        flash("Upload your CV before tailoring applications.", "warning")
        return redirect(url_for("main.dashboard"))
    result = ai.tailor_application(
        user["cv_text"], user["full_name"], app["job_title"],
        app["company_name"], app["job_description"],
    )
    execute(
        "UPDATE applications SET tailored_cv = ?, cover_letter = ?, status = 'tailored' WHERE id = ?",
        (result["tailored_cv"], result["cover_letter"], app_id),
    )
    flash(f"Tailored application ready ({result['powered_by']}). Review it before sending.", "success")
    return redirect(url_for("main.job_view", app_id=app_id))


@bp.route("/jobs/<int:app_id>")
@login_required  # viewing your own data stays available after the trial ends
def job_view(app_id):
    app = _owned_app(app_id)
    return render_template("application.html", app=app)


@bp.route("/jobs/<int:app_id>/edit", methods=["POST"])
@login_required  # editing existing data is allowed after the trial ends
def job_edit(app_id):
    _owned_app(app_id)
    execute(
        "UPDATE applications SET cover_letter = ?, tailored_cv = ? WHERE id = ?",
        (request.form.get("cover_letter", ""), request.form.get("tailored_cv", ""), app_id),
    )
    flash("Saved your edits.", "success")
    return redirect(url_for("main.job_view", app_id=app_id))


@bp.route("/jobs/<int:app_id>/cv.pdf")
@login_required  # previewing your own CV PDF is a view action
def job_cv_pdf(app_id):
    """Inline preview of the exact PDF that will be attached when sending."""
    app = _owned_app(app_id)
    user = query("SELECT * FROM users WHERE id = ?", (g.user["id"],), one=True)
    if not (app["tailored_cv"] or user["cv_text"]).strip():
        flash("Upload your CV (or tailor this application) before previewing the PDF.", "warning")
        return redirect(url_for("main.job_view", app_id=app_id))
    cv_bytes, cv_name = _cv_pdf(user, app)
    resp = Response(cv_bytes, mimetype="application/pdf")
    safe_name = cv_name.replace('"', "").replace("\r", "").replace("\n", "")
    resp.headers["Content-Disposition"] = f'inline; filename="{safe_name}"'
    resp.headers["Cache-Control"] = "no-store"  # always re-render the latest CV
    return resp


@bp.route("/jobs/<int:app_id>/send", methods=["POST"])
@active_required
def job_send(app_id):
    app = _owned_app(app_id)
    ok, msg = _send_one(app)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("main.job_view", app_id=app_id))


@bp.route("/jobs/send-all", methods=["POST"])
@active_required
def job_send_all():
    pending = query(
        "SELECT * FROM applications WHERE user_id = ? AND status IN ('tailored','draft') ORDER BY created_at",
        (g.user["id"],),
    )
    sent = failed = 0
    for app in pending:
        ok, _ = _send_one(app)
        if ok:
            sent += 1
        else:
            failed += 1
            # stop early on rate limit so we don't spin
            if _sent_last_hour(g.user["id"]) >= current_app.config["SEND_RATE_PER_HOUR"]:
                break
    flash(f"Sent {sent} application(s); {failed} failed or skipped.", "info")
    return redirect(url_for("main.dashboard"))


@bp.route("/jobs/<int:app_id>/delete", methods=["POST"])
@login_required  # managing/deleting your own data is allowed after the trial
def job_delete(app_id):
    _owned_app(app_id)
    execute("DELETE FROM applications WHERE id = ?", (app_id,))
    flash("Application deleted.", "info")
    return redirect(url_for("main.dashboard"))


# ------------------------------------------------------------------ sending helpers
def _cv_pdf(user, app):
    """Build the CV PDF for an application — used by both preview and send,
    so what the user previews is byte-for-byte what the company receives."""
    cv_content = app["tailored_cv"] or user["cv_text"]
    contact = " | ".join(p for p in (user["smtp_user"], user["phone"]) if p)
    cv_bytes = pdf.build_pdf(user["full_name"], cv_content, contact_line=contact)
    return cv_bytes, f"CV - {user['full_name']}.pdf"


def _sent_last_hour(user_id):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    row = query(
        "SELECT COUNT(*) AS c FROM applications WHERE user_id = ? AND status = 'sent' AND sent_at >= ?",
        (user_id, cutoff), one=True,
    )
    return row["c"] if row else 0


def _send_one(app):
    user = query("SELECT * FROM users WHERE id = ?", (g.user["id"],), one=True)
    if _sent_last_hour(user["id"]) >= current_app.config["SEND_RATE_PER_HOUR"]:
        return False, "Hourly send limit reached. Try again later (this protects against spam blocks)."

    cover = app["cover_letter"] or "Please find my application and CV attached."
    subject = (
        f"Application: {app['job_title']}" if app["job_title"]
        else f"Job application from {user['full_name']}"
    )
    cv_bytes, cv_name = _cv_pdf(user, app)

    try:
        send_application(
            user, current_app.config, app["company_email"], subject, cover,
            attachment_name=cv_name, attachment_bytes=cv_bytes,
        )
    except EmailError as exc:
        execute("UPDATE applications SET status = 'failed', error = ? WHERE id = ?",
                (str(exc), app["id"]))
        return False, str(exc)

    execute("UPDATE applications SET status = 'sent', sent_at = ?, error = '' WHERE id = ?",
            (now_iso(), app["id"]))
    return True, f"Application sent to {app['company_email']}."


# ------------------------------------------------------------------ settings
@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        try:
            smtp_port = int(request.form.get("smtp_port") or 587)
        except ValueError:
            smtp_port = 587
        smtp_port = min(max(smtp_port, 1), 65535)
        # Blank password = keep the existing one (it is never echoed to the page).
        smtp_password = request.form.get("smtp_password", "") or g.user["smtp_password"]
        execute(
            """UPDATE users SET full_name = ?, phone = ?, smtp_host = ?, smtp_port = ?,
               smtp_user = ?, smtp_password = ?, from_name = ? WHERE id = ?""",
            (
                request.form.get("full_name", "").strip()[:MAX_FIELD],
                request.form.get("phone", "").strip()[:MAX_FIELD],
                request.form.get("smtp_host", "").strip()[:MAX_FIELD],
                smtp_port,
                request.form.get("smtp_user", "").strip()[:MAX_FIELD],
                smtp_password,
                request.form.get("from_name", "").strip()[:MAX_FIELD],
                g.user["id"],
            ),
        )
        flash("Settings saved.", "success")
        return redirect(url_for("main.settings"))
    user = query("SELECT * FROM users WHERE id = ?", (g.user["id"],), one=True)
    return render_template("settings.html", u=user)

"""Core user-facing routes: dashboard, CV, job applications, settings."""
from datetime import datetime, timezone, timedelta
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, g, current_app, abort
)

from .auth import login_required, active_required, access_active
from .db import query, execute, now_iso
from .services import cv_parser, ai, pdf
from .services.emailer import send_application, EmailError

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    if g.user:
        return redirect(url_for("main.dashboard"))
    return render_template("index.html")


@bp.route("/dashboard")
@login_required
def dashboard():
    apps = query(
        "SELECT * FROM applications WHERE user_id = ? ORDER BY created_at DESC",
        (g.user["id"],),
    )
    return render_template(
        "dashboard.html",
        apps=apps,
        is_active=access_active(g.user),
        sent_last_hour=_sent_last_hour(g.user["id"]),
        rate_limit=current_app.config["SEND_RATE_PER_HOUR"],
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
        "UPDATE users SET cv_text = ?, cv_filename = ? WHERE id = ?",
        (text, file.filename, g.user["id"]),
    )
    flash("CV uploaded and read successfully.", "success")
    return redirect(url_for("main.dashboard"))


@bp.route("/cv/review", methods=["POST"])
@active_required
def cv_review():
    user = query("SELECT * FROM users WHERE id = ?", (g.user["id"],), one=True)
    result = ai.review_cv(user["cv_text"])
    apps = query(
        "SELECT * FROM applications WHERE user_id = ? ORDER BY created_at DESC",
        (g.user["id"],),
    )
    return render_template(
        "dashboard.html",
        apps=apps,
        is_active=True,
        review=result,
        sent_last_hour=_sent_last_hour(g.user["id"]),
        rate_limit=current_app.config["SEND_RATE_PER_HOUR"],
    )


# ------------------------------------------------------------------ job applications
@bp.route("/jobs/add", methods=["POST"])
@active_required
def job_add():
    company = request.form.get("company_name", "").strip()
    email = request.form.get("company_email", "").strip()
    title = request.form.get("job_title", "").strip()
    desc = request.form.get("job_description", "").strip()
    if not email or "@" not in email:
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
@active_required
def job_view(app_id):
    app = _owned_app(app_id)
    return render_template("application.html", app=app)


@bp.route("/jobs/<int:app_id>/edit", methods=["POST"])
@active_required
def job_edit(app_id):
    _owned_app(app_id)
    execute(
        "UPDATE applications SET cover_letter = ?, tailored_cv = ? WHERE id = ?",
        (request.form.get("cover_letter", ""), request.form.get("tailored_cv", ""), app_id),
    )
    flash("Saved your edits.", "success")
    return redirect(url_for("main.job_view", app_id=app_id))


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
@active_required
def job_delete(app_id):
    _owned_app(app_id)
    execute("DELETE FROM applications WHERE id = ?", (app_id,))
    flash("Application deleted.", "info")
    return redirect(url_for("main.dashboard"))


# ------------------------------------------------------------------ sending helpers
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
    cv_content = app["tailored_cv"] or user["cv_text"]
    contact = " | ".join(p for p in (user["smtp_user"], user["phone"]) if p)
    cv_bytes = pdf.build_pdf(user["full_name"], cv_content, contact_line=contact)
    cv_name = f"CV - {user['full_name']}.pdf"

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
        execute(
            """UPDATE users SET full_name = ?, phone = ?, smtp_host = ?, smtp_port = ?,
               smtp_user = ?, smtp_password = ?, from_name = ? WHERE id = ?""",
            (
                request.form.get("full_name", "").strip(),
                request.form.get("phone", "").strip(),
                request.form.get("smtp_host", "").strip(),
                int(request.form.get("smtp_port") or 587),
                request.form.get("smtp_user", "").strip(),
                request.form.get("smtp_password", ""),
                request.form.get("from_name", "").strip(),
                g.user["id"],
            ),
        )
        flash("Settings saved.", "success")
        return redirect(url_for("main.settings"))
    user = query("SELECT * FROM users WHERE id = ?", (g.user["id"],), one=True)
    return render_template("settings.html", u=user)

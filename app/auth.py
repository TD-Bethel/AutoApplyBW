"""Authentication and access control.

Access model (owner stays in control):
  - A new user registers -> status = 'pending'.
  - They pay the owner offline (Orange Money / MyZaka / bank transfer).
  - The owner sets them to 'active' (optionally with an expiry date) in /admin.
  - AI + email features require an 'active', non-expired account.
"""
import functools
import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone
from flask import (
    Blueprint, render_template, request, redirect, url_for, session, flash, g, abort,
    current_app,
)
from werkzeug.security import generate_password_hash, check_password_hash

from .db import query, execute, now_iso
from .security import (
    valid_email, safe_next, too_many_attempts, record_failed_attempt, clear_attempts,
    rate_limited,
)
from .services.emailer import send_system_email, EmailError

bp = Blueprint("auth", __name__)

# Constant-time-ish login even when the email doesn't exist: always verify
# against some hash so attackers can't probe which emails are registered.
_DUMMY_HASH = generate_password_hash("not-a-real-password")

MAX_EMAIL = 254
MAX_NAME = 120
MAX_PHONE = 40
MAX_PASSWORD = 200


def load_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    user = query("SELECT * FROM users WHERE id = ?", (uid,), one=True)
    if user is not None and user["status"] == "suspended":
        # Suspension takes effect immediately, not just at next login.
        session.clear()
        return None
    return user


@bp.before_app_request
def attach_user():
    g.user = load_user()


def access_active(user):
    """True if the user has a PAID, non-expired subscription right now."""
    if user is None or user["status"] != "active":
        return False
    if user["access_expires"]:
        try:
            if date.fromisoformat(user["access_expires"]) < date.today():
                return False
        except ValueError:
            return False  # unparseable expiry = no access, never silent unlimited
    return True


# ------------------------------------------------------------------ access
# The app is FREE for everyone — there is no trial or subscription. These helpers
# stay (returning "no trial" / "full access") so the routes and templates that
# call them keep working without change.
def trial_state(user):
    return None


def trial_active(user):
    return False


def feature_access(user):
    """Free for all: any logged-in user gets the full app."""
    return user is not None


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please log in first.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def active_required(view):
    """Gate key actions: allow paid users and users still inside their free
    trial; block (with an upgrade message) once the trial is used up."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        if not feature_access(g.user):
            state = trial_state(g.user)
            if state and state["over"]:
                flash("Your free trial has ended. Pay your subscription and contact "
                      "the admin (or use the support chat) to unlock full access. "
                      "You can still view your data.", "warning")
            else:
                flash("Your account is not active yet. Please complete payment to get access.",
                      "warning")
            return redirect(url_for("main.dashboard"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None or g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        if rate_limited("register", 10):
            flash("Too many sign-up attempts from your network. Please wait a few "
                  "minutes and try again.", "danger")
            return render_template("register.html")
        email = request.form.get("email", "").strip().lower()[:MAX_EMAIL]
        password = request.form.get("password", "")[:MAX_PASSWORD]
        full_name = request.form.get("full_name", "").strip()[:MAX_NAME]
        phone = request.form.get("phone", "").strip()[:MAX_PHONE]

        error = None
        if not valid_email(email):
            error = "A valid email is required."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif not full_name:
            error = "Please enter your full name."
        elif query("SELECT id FROM users WHERE email = ?", (email,), one=True):
            error = "An account with that email already exists."

        if error:
            flash(error, "danger")
        else:
            execute(
                """INSERT INTO users (email, password_hash, full_name, phone, role, status, created_at)
                   VALUES (?, ?, ?, ?, 'user', 'pending', ?)""",
                (email, generate_password_hash(password), full_name, phone, now_iso()),
            )
            flash("Account created! Log in to start — AutoApply BW is free to use.",
                  "success")
            return redirect(url_for("auth.login"))
    return render_template("register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:MAX_EMAIL]
        password = request.form.get("password", "")[:MAX_PASSWORD]

        if too_many_attempts(email):
            flash("Too many failed attempts. Please wait 15 minutes and try again.", "danger")
            return render_template("login.html")

        user = query("SELECT * FROM users WHERE email = ?", (email,), one=True)
        password_ok = check_password_hash(
            user["password_hash"] if user else _DUMMY_HASH, password
        )
        if user is None or not password_ok:
            record_failed_attempt(email)
            flash("Incorrect email or password.", "danger")
        elif user["status"] == "suspended":
            flash("This account has been suspended. Contact the admin.", "danger")
        else:
            clear_attempts(email)
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            nxt = safe_next(request.args.get("next"))
            return redirect(nxt or url_for("main.dashboard"))
    return render_template("login.html")


@bp.route("/password", methods=["POST"])
@login_required
def change_password():
    current = request.form.get("current_password", "")[:MAX_PASSWORD]
    new = request.form.get("new_password", "")[:MAX_PASSWORD]
    if not check_password_hash(g.user["password_hash"], current):
        flash("Your current password is incorrect.", "danger")
    elif len(new) < 8:
        flash("New password must be at least 8 characters.", "danger")
    else:
        execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new), g.user["id"]),
        )
        flash("Password changed.", "success")
    return redirect(url_for("main.settings"))


# ------------------------------------------------------------------ forgot password
RESET_TTL_MINUTES = 30


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


@bp.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        # Throttle so this can't be abused to spam reset emails / burn SMTP quota.
        if rate_limited("forgot", 5):
            flash("Too many reset requests. Please wait a few minutes and try again.",
                  "warning")
            return redirect(url_for("auth.login"))
        email = request.form.get("email", "").strip().lower()[:MAX_EMAIL]
        user = query("SELECT * FROM users WHERE email = ?", (email,), one=True)
        if user:
            # Invalidate any earlier tokens for this user, then issue a fresh one.
            execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
            token = secrets.token_urlsafe(32)
            expires = (datetime.now(timezone.utc)
                       + timedelta(minutes=RESET_TTL_MINUTES)).isoformat()
            execute(
                "INSERT INTO password_resets (token_hash, user_id, expires_at, created_at)"
                " VALUES (?, ?, ?, ?)",
                (_hash_token(token), user["id"], expires, now_iso()),
            )
            link = url_for("auth.reset_password", token=token, _external=True)
            body = (
                f"Hello {user['full_name'] or ''},\n\n"
                f"We received a request to reset your AutoApply BW password. "
                f"Click the link below to choose a new password. It expires in "
                f"{RESET_TTL_MINUTES} minutes and can only be used once.\n\n"
                f"{link}\n\n"
                f"If you did not request this, you can ignore this email; your "
                f"password will not change.\n\nAutoApply BW"
            )
            try:
                send_system_email(current_app.config, user["email"],
                                  "Reset your AutoApply BW password", body)
            except EmailError as exc:
                current_app.logger.warning("Password reset email failed: %s", exc)
        # Always show the same message — never reveal whether an email is registered.
        flash("If that email is registered, a reset link has been sent. "
              "Check your inbox (and spam folder).", "info")
        return redirect(url_for("auth.login"))
    return render_template("forgot_password.html")


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    if g.user:
        return redirect(url_for("main.dashboard"))
    row = query("SELECT * FROM password_resets WHERE token_hash = ?",
                (_hash_token(token),), one=True)
    valid = bool(row) and not row["used"]
    if valid:
        try:
            valid = datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc)
        except ValueError:
            valid = False
    if not valid:
        flash("That reset link is invalid or has expired. Please request a new one.",
              "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new = request.form.get("password", "")[:MAX_PASSWORD]
        confirm = request.form.get("confirm", "")[:MAX_PASSWORD]
        if len(new) < 8:
            flash("Password must be at least 8 characters.", "danger")
        elif new != confirm:
            flash("The two passwords do not match.", "danger")
        else:
            execute("UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new), row["user_id"]))
            execute("UPDATE password_resets SET used = 1 WHERE token_hash = ?",
                    (row["token_hash"],))
            flash("Your password has been reset. You can now log in.", "success")
            return redirect(url_for("auth.login"))
    return render_template("reset_password.html", token=token)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))

"""Authentication and access control.

Access model (owner stays in control):
  - A new user registers -> status = 'pending'.
  - They pay the owner offline (Orange Money / MyZaka / bank transfer).
  - The owner sets them to 'active' (optionally with an expiry date) in /admin.
  - AI + email features require an 'active', non-expired account.
"""
import functools
from datetime import date
from flask import (
    Blueprint, render_template, request, redirect, url_for, session, flash, g, abort
)
from werkzeug.security import generate_password_hash, check_password_hash

from .db import query, execute, now_iso
from .security import (
    valid_email, safe_next, too_many_attempts, record_failed_attempt, clear_attempts,
)

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
    """True if the user may use paid features right now."""
    if user is None or user["status"] != "active":
        return False
    if user["access_expires"]:
        try:
            if date.fromisoformat(user["access_expires"]) < date.today():
                return False
        except ValueError:
            return False  # unparseable expiry = no access, never silent unlimited
    return True


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please log in first.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def active_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        if not access_active(g.user):
            flash("Your account is not active yet. Please complete payment to get access.", "warning")
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
            flash("Account created! It is pending activation. Pay and contact the admin to be activated.", "success")
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


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))

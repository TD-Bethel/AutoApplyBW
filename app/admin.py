"""Owner/admin panel: control who has access (the paywall is enforced here)."""
from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash, g

from .auth import admin_required
from .db import query, execute

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@admin_required
def index():
    users = query(
        """SELECT u.*,
                  (SELECT COUNT(*) FROM applications a WHERE a.user_id = u.id) AS app_count,
                  (SELECT COUNT(*) FROM applications a WHERE a.user_id = u.id AND a.status='sent') AS sent_count
           FROM users u ORDER BY u.created_at DESC"""
    )
    stats = {
        "total": len(users),
        "active": sum(1 for u in users if u["status"] == "active"),
        "pending": sum(1 for u in users if u["status"] == "pending"),
    }
    return render_template("admin.html", users=users, stats=stats)


@bp.route("/users/<int:user_id>/activate", methods=["POST"])
@admin_required
def activate(user_id):
    expires = request.form.get("access_expires", "").strip() or None
    if expires:
        try:
            date.fromisoformat(expires)
        except ValueError:
            flash("Invalid expiry date. Use the date picker (YYYY-MM-DD) or leave it blank.", "danger")
            return redirect(url_for("admin.index"))
    execute(
        "UPDATE users SET status = 'active', access_expires = ? WHERE id = ?",
        (expires, user_id),
    )
    flash("User activated.", "success")
    return redirect(url_for("admin.index"))


@bp.route("/users/<int:user_id>/suspend", methods=["POST"])
@admin_required
def suspend(user_id):
    if user_id == g.user["id"]:
        flash("You cannot suspend your own admin account.", "danger")
        return redirect(url_for("admin.index"))
    execute("UPDATE users SET status = 'suspended' WHERE id = ?", (user_id,))
    flash("User suspended.", "info")
    return redirect(url_for("admin.index"))


@bp.route("/users/<int:user_id>/pending", methods=["POST"])
@admin_required
def set_pending(user_id):
    execute("UPDATE users SET status = 'pending', access_expires = NULL WHERE id = ?", (user_id,))
    flash("User set back to pending.", "info")
    return redirect(url_for("admin.index"))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    target = query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if target is None:
        flash("User not found.", "danger")
    elif target["role"] == "admin":
        flash("Admin accounts cannot be deleted from the panel.", "danger")
    else:
        execute("DELETE FROM applications WHERE user_id = ?", (user_id,))
        execute("DELETE FROM users WHERE id = ?", (user_id,))
        flash(f"Deleted {target['email']} and their applications.", "info")
    return redirect(url_for("admin.index"))

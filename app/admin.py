"""Owner/admin panel: control who has access (the paywall is enforced here)."""
from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash, g

from .auth import admin_required
from .db import query, execute, now_iso

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
    support_threads = query(
        "SELECT COUNT(DISTINCT user_id) AS c FROM support_messages", one=True
    )["c"]
    return render_template("admin.html", users=users, stats=stats,
                           support_threads=support_threads)


# ------------------------------------------------------------------ user CVs
@bp.route("/users/<int:user_id>/cv")
@admin_required
def view_cv(user_id):
    user = query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if user is None:
        flash("User not found.", "danger")
        return redirect(url_for("admin.index"))
    return render_template("admin_cv.html", u=user)


@bp.route("/users/<int:user_id>/cv.pdf")
@admin_required
def view_cv_pdf(user_id):
    from flask import Response
    from .services import pdf
    user = query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if user is None or not (user["cv_text"] or "").strip():
        flash("This user has no CV on file.", "warning")
        return redirect(url_for("admin.index"))
    contact = " | ".join(p for p in (user["smtp_user"] or user["email"], user["phone"]) if p)
    data = pdf.build_pdf(user["full_name"] or user["email"], user["cv_text"], contact_line=contact)
    resp = Response(data, mimetype="application/pdf")
    safe = (user["full_name"] or "user").replace('"', "").replace("\r", "").replace("\n", "")
    resp.headers["Content-Disposition"] = f'inline; filename="CV - {safe}.pdf"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------ support inbox
@bp.route("/support")
@admin_required
def support_inbox():
    threads = query(
        """SELECT u.id, u.email, u.full_name, u.status,
                  MAX(m.created_at) AS last_at,
                  (SELECT content FROM support_messages
                    WHERE user_id = u.id ORDER BY id DESC LIMIT 1) AS last_message,
                  SUM(CASE WHEN m.role = 'user' THEN 1 ELSE 0 END) AS user_msgs
           FROM support_messages m JOIN users u ON u.id = m.user_id
           GROUP BY u.id ORDER BY last_at DESC"""
    )
    return render_template("admin_support.html", threads=threads)


@bp.route("/support/<int:user_id>", methods=["GET", "POST"])
@admin_required
def support_thread(user_id):
    user = query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if user is None:
        flash("User not found.", "danger")
        return redirect(url_for("admin.support_inbox"))
    if request.method == "POST":
        text = request.form.get("message", "").strip()[:1000]
        if text:
            execute(
                "INSERT INTO support_messages (user_id, role, content, created_at)"
                " VALUES (?, 'admin', ?, ?)",
                (user_id, text, now_iso()),
            )
            flash("Reply sent — the user will see it in their chat widget.", "success")
        return redirect(url_for("admin.support_thread", user_id=user_id))
    thread = query(
        "SELECT * FROM support_messages WHERE user_id = ? ORDER BY id", (user_id,)
    )
    return render_template("admin_support_thread.html", u=user, thread=thread)


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
        execute("DELETE FROM payments WHERE user_id = ?", (user_id,))
        execute("DELETE FROM users WHERE id = ?", (user_id,))
        flash(f"Deleted {target['email']} and their applications.", "info")
    return redirect(url_for("admin.index"))

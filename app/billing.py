"""Self-service card payments (Flutterwave hosted checkout).

Replaces the old offline (Orange Money / MyZaka) flow: a successful payment
flips the user to 'active' and extends access_expires by the configured number
of days. Admin manual activation in /admin stays as a backstop.

Two paths confirm a payment, and both go through _settle() so it is verified
server-side and applied exactly once (idempotent):
  - /billing/callback  — the browser redirect back from Flutterwave (user-facing).
  - /billing/webhook   — Flutterwave's server-to-server notification (authoritative,
                         in case the user closes the tab before the redirect).
"""
import secrets
from datetime import date, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, g, current_app)

from .auth import login_required, access_active
from .db import query, execute, now_iso
from .services import payments

bp = Blueprint("billing", __name__, url_prefix="/billing")


def _price(config):
    try:
        return float(config.get("SUBSCRIPTION_PRICE_BWP") or 0)
    except (TypeError, ValueError):
        return 0.0


def _days(config):
    try:
        return int(config.get("SUBSCRIPTION_DAYS") or 30)
    except (TypeError, ValueError):
        return 30


@bp.route("/")
@login_required
def index():
    cfg = current_app.config
    return render_template(
        "billing.html",
        price=_price(cfg),
        days=_days(cfg),
        currency=cfg.get("SUBSCRIPTION_CURRENCY", "BWP"),
        configured=payments.configured(cfg),
        is_active=access_active(g.user),
        expires=g.user["access_expires"],
    )


@bp.route("/checkout", methods=["POST"])
@login_required
def checkout():
    cfg = current_app.config
    if not payments.configured(cfg):
        flash("Card payment is not available yet. Please contact the admin.", "danger")
        return redirect(url_for("billing.index"))
    amount = _price(cfg)
    if amount <= 0:
        flash("Subscription price is not set. Please contact the admin.", "danger")
        return redirect(url_for("billing.index"))
    currency = cfg.get("SUBSCRIPTION_CURRENCY", "BWP")
    days = _days(cfg)
    # Our own reference; ties the provider's transaction back to this user/row.
    tx_ref = f"abw-{g.user['id']}-{secrets.token_hex(8)}"
    execute(
        "INSERT INTO payments (user_id, tx_ref, amount, currency, days, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (g.user["id"], tx_ref, amount, currency, days, now_iso()),
    )
    try:
        link = payments.create_checkout(
            cfg, tx_ref=tx_ref, amount=amount, currency=currency,
            customer_email=g.user["email"], customer_name=g.user["full_name"],
            redirect_url=url_for("billing.callback", _external=True),
        )
    except payments.PaymentError as exc:
        current_app.logger.warning("Checkout failed for %s: %s", tx_ref, exc)
        flash("Could not start the payment. Please try again in a moment.", "danger")
        return redirect(url_for("billing.index"))
    return redirect(link)


def _apply_payment(payment, flw_tx_id):
    """Idempotently grant access for a verified, successful payment."""
    if payment["status"] == "successful":
        return  # already applied — never double-extend
    user = query("SELECT * FROM users WHERE id = ?", (payment["user_id"],), one=True)
    if user is None:
        return
    # Extend from the later of today or an existing (still-valid) expiry, so a
    # renewal stacks remaining time instead of shortening it.
    base = date.today()
    if user["access_expires"]:
        try:
            current = date.fromisoformat(user["access_expires"])
            if current > base:
                base = current
        except ValueError:
            pass
    new_expiry = (base + timedelta(days=payment["days"])).isoformat()
    execute("UPDATE users SET status = 'active', access_expires = ? WHERE id = ?",
            (new_expiry, payment["user_id"]))
    execute("UPDATE payments SET status = 'successful', flw_tx_id = ?, paid_at = ? WHERE id = ?",
            (str(flw_tx_id), now_iso(), payment["id"]))


def _settle(tx_ref, flw_tx_id):
    """Verify a transaction with Flutterwave and apply it if genuinely paid.
    Returns True if access is (now or already) granted."""
    payment = query("SELECT * FROM payments WHERE tx_ref = ?", (tx_ref,), one=True)
    if payment is None:
        return False
    if payment["status"] == "successful":
        return True  # idempotent: a prior callback/webhook already settled it
    try:
        data = payments.verify_transaction(current_app.config, flw_tx_id)
    except payments.PaymentError as exc:
        current_app.logger.warning("Verify failed for %s: %s", tx_ref, exc)
        return False
    # Trust only the provider's verified figures, and only if they match what we
    # asked this user to pay — guards against tampered redirects and replays.
    try:
        paid_amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        paid_amount = 0.0
    ok = (data.get("status") == "successful"
          and data.get("tx_ref") == tx_ref
          and data.get("currency") == payment["currency"]
          and paid_amount >= float(payment["amount"]))
    if not ok:
        execute("UPDATE payments SET status = 'failed' WHERE id = ? AND status = 'pending'",
                (payment["id"],))
        return False
    _apply_payment(payment, data.get("id") or flw_tx_id)
    return True


@bp.route("/callback")
@login_required
def callback():
    status = request.args.get("status", "")
    tx_ref = request.args.get("tx_ref", "")
    flw_tx_id = request.args.get("transaction_id", "")
    if status == "successful" and tx_ref and flw_tx_id and _settle(tx_ref, flw_tx_id):
        flash("Payment received — your account is now active. Thank you!", "success")
    else:
        flash("Payment was not completed. If you were charged but still see this, "
              "contact support and we'll sort it out.", "warning")
    return redirect(url_for("main.dashboard"))


@bp.route("/webhook", methods=["POST"])
def webhook():
    """Flutterwave server-to-server confirmation. CSRF-exempt by design (there is
    no session/form); authenticity is proven by the shared secret-hash header
    that the owner configures in the Flutterwave dashboard."""
    expected = current_app.config.get("FLW_WEBHOOK_HASH", "")
    received = request.headers.get("verif-hash", "")
    if not expected or not secrets.compare_digest(received, expected):
        return ("", 401)
    event = request.get_json(silent=True) or {}
    data = event.get("data") or {}
    tx_ref = data.get("tx_ref", "")
    flw_tx_id = data.get("id", "")
    if tx_ref and flw_tx_id:
        _settle(tx_ref, str(flw_tx_id))
    # Always 200 once authenticated so Flutterwave stops retrying a handled event.
    return ("", 200)

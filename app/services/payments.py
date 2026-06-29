"""Card payments via Flutterwave (hosted checkout).

We talk to Flutterwave over plain HTTPS/JSON with `requests` only — no SDK — to
keep the app dependency-light. We never see or store card data: the user is
redirected to Flutterwave's hosted page and returns with a transaction id that
we verify server-side (the callback's query string is never trusted on its own).

The owner sets FLW_SECRET_KEY (+ FLW_WEBHOOK_HASH) in .env. With no key set,
`configured()` is False and the billing routes degrade to a "not available yet"
notice — nothing here is a hard requirement to run the app.
"""
import requests

API_BASE = "https://api.flutterwave.com/v3"
TIMEOUT = 20  # seconds; never hold a request thread on a hung provider


class PaymentError(RuntimeError):
    """A payment could not be created or verified (provider/network/validation)."""


def configured(config):
    """True when card payment is wired up (a secret key is present)."""
    return bool(config.get("FLW_SECRET_KEY"))


def _headers(config):
    return {
        "Authorization": f"Bearer {config['FLW_SECRET_KEY']}",
        "Content-Type": "application/json",
    }


def create_checkout(config, *, tx_ref, amount, currency, customer_email,
                    customer_name, redirect_url):
    """Create a hosted-checkout session; return the URL to redirect the user to."""
    payload = {
        "tx_ref": tx_ref,
        "amount": str(amount),
        "currency": currency,
        "redirect_url": redirect_url,
        "payment_options": "card",
        "customer": {
            "email": customer_email,
            "name": customer_name or customer_email,
        },
        "customizations": {
            "title": "AutoApply BW subscription",
            "description": "Full access to CV tailoring, job search and sending.",
        },
    }
    try:
        resp = requests.post(f"{API_BASE}/payments", json=payload,
                             headers=_headers(config), timeout=TIMEOUT)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentError(f"Could not reach the payment provider: {exc}") from exc
    if resp.status_code >= 400 or data.get("status") != "success":
        raise PaymentError(data.get("message") or "Payment initialisation failed.")
    link = (data.get("data") or {}).get("link")
    if not link:
        raise PaymentError("Payment provider did not return a checkout link.")
    return link


def verify_transaction(config, flw_tx_id):
    """Verify a transaction by the provider's transaction id. Returns the
    provider's transaction data dict (status, amount, currency, tx_ref, id, ...).
    The caller MUST check those figures against what was expected."""
    try:
        resp = requests.get(f"{API_BASE}/transactions/{flw_tx_id}/verify",
                            headers=_headers(config), timeout=TIMEOUT)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentError(f"Could not verify the payment: {exc}") from exc
    if data.get("status") != "success":
        raise PaymentError(data.get("message") or "Verification failed.")
    return data.get("data") or {}

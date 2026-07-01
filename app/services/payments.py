"""Card payments via a hosted-checkout gateway.

Two providers are supported and chosen with PAYMENT_PROVIDER (default "dpo"):

  - "dpo"          DPO Pay / Direct Pay Online (XML API) — the Botswana default,
                   takes Visa/Mastercard and mobile money in Pula.
  - "flutterwave"  Flutterwave (JSON API).

Both follow the same shape and are talked to with `requests` only (no SDK), to
keep the app dependency-light: create a checkout (redirect the user to the
gateway's hosted page) and later verify the transaction server-side before
granting access. We never see or store card data. With no keys set for the
chosen provider, `configured()` is False and billing degrades to a
"not available" notice — nothing here is a hard requirement to run the app.

The public interface the billing routes use:
  configured(config)                         -> bool
  create_checkout(config, ...)               -> (checkout_url, provider_ref)
  verify(config, payment_row, request_args)  -> bool   (genuinely paid?)
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

TIMEOUT = 25  # seconds; never hold a request thread on a hung provider


class PaymentError(RuntimeError):
    """A payment could not be created or verified (provider/network/validation)."""


def provider(config):
    return (config.get("PAYMENT_PROVIDER") or "dpo").strip().lower()


def configured(config):
    """True when the chosen provider has its credentials set."""
    p = provider(config)
    if p == "mock":
        return True   # test mode needs no credentials
    if p == "flutterwave":
        return bool(config.get("FLW_SECRET_KEY"))
    return bool(config.get("DPO_COMPANY_TOKEN"))


def create_checkout(config, *, tx_ref, amount, currency, customer_email,
                    customer_name, redirect_url, back_url):
    """Create a hosted checkout. Returns (checkout_url, provider_ref).

    provider_ref is the gateway's own transaction reference (DPO's token), stored
    so the payment can be verified later; it is "" for providers that only supply
    it on the return trip (Flutterwave)."""
    if provider(config) == "flutterwave":
        return _flw_create(config, tx_ref=tx_ref, amount=amount, currency=currency,
                           customer_email=customer_email, customer_name=customer_name,
                           redirect_url=redirect_url), ""
    return _dpo_create(config, tx_ref=tx_ref, amount=amount, currency=currency,
                       customer_email=customer_email, customer_name=customer_name,
                       redirect_url=redirect_url, back_url=back_url)


def verify(config, payment, request_args):
    """Return True iff the gateway confirms `payment` is genuinely paid for at
    least its amount in its currency. `request_args` is the callback query (or a
    webhook body). All provider-specific matching lives in the helpers."""
    p = provider(config)
    if p == "mock":
        return True   # test mode: the internal confirm page stands in for a gateway
    if p == "flutterwave":
        return _flw_verify(config, payment, request_args)
    return _dpo_verify(config, payment, request_args)


# ==========================================================================
# DPO Pay (Direct Pay Online) — XML API
# ==========================================================================
DPO_API_DEFAULT = "https://secure.3gdirectpay.com/API/v6/"
DPO_PAY_DEFAULT = "https://secure.3gdirectpay.com/payv3.php"


def _dpo_text(root, tag):
    el = root.find(tag)
    return el.text.strip() if el is not None and el.text else ""


def _dpo_post(config, api3g):
    """POST an <API3G> element and return the parsed response root."""
    body = '<?xml version="1.0" encoding="utf-8"?>' + ET.tostring(api3g, encoding="unicode")
    api = config.get("DPO_API_URL") or DPO_API_DEFAULT
    # A real User-Agent + Accept: some WAFs in front of the API (CloudFront) 403
    # the default python-requests client.
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
        "User-Agent": "Mozilla/5.0 (AutoApply BW payments)",
    }
    try:
        resp = requests.post(api, data=body.encode("utf-8"), headers=headers, timeout=TIMEOUT)
        return ET.fromstring(resp.text)
    except (requests.RequestException, ET.ParseError) as exc:
        raise PaymentError(f"Could not reach the payment provider: {exc}") from exc


def _dpo_create(config, *, tx_ref, amount, currency, customer_email,
                customer_name, redirect_url, back_url):
    # ElementTree escapes text on serialise, so user-supplied names/emails can't
    # break out of the XML.
    api3g = ET.Element("API3G")
    ET.SubElement(api3g, "CompanyToken").text = config["DPO_COMPANY_TOKEN"]
    ET.SubElement(api3g, "Request").text = "createToken"
    txn = ET.SubElement(api3g, "Transaction")
    ET.SubElement(txn, "PaymentAmount").text = f"{float(amount):.2f}"
    ET.SubElement(txn, "PaymentCurrency").text = currency
    ET.SubElement(txn, "CompanyRef").text = tx_ref
    ET.SubElement(txn, "RedirectURL").text = redirect_url
    ET.SubElement(txn, "BackURL").text = back_url
    ET.SubElement(txn, "CompanyRefUnique").text = "1"
    ET.SubElement(txn, "PTL").text = "24"          # payment time limit, hours
    if customer_email:
        ET.SubElement(txn, "customerEmail").text = customer_email
    if customer_name:
        parts = customer_name.split()
        ET.SubElement(txn, "customerFirstName").text = parts[0]
        ET.SubElement(txn, "customerLastName").text = " ".join(parts[1:]) or parts[0]
    services = ET.SubElement(api3g, "Services")
    service = ET.SubElement(services, "Service")
    ET.SubElement(service, "ServiceType").text = config.get("DPO_SERVICE_TYPE", "")
    ET.SubElement(service, "ServiceDescription").text = "AutoApply BW subscription"
    ET.SubElement(service, "ServiceDate").text = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")

    root = _dpo_post(config, api3g)
    if _dpo_text(root, "Result") != "000":
        raise PaymentError(_dpo_text(root, "ResultExplanation") or "DPO could not create the payment.")
    token = _dpo_text(root, "TransToken")
    if not token:
        raise PaymentError("DPO did not return a transaction token.")
    pay = config.get("DPO_PAY_URL") or DPO_PAY_DEFAULT
    return f"{pay}?ID={token}", token


def _dpo_verify(config, payment, request_args):
    # The token we stored at checkout is the source of truth; fall back to what
    # DPO put on the redirect if for some reason it wasn't stored.
    token = (payment["flw_tx_id"] or request_args.get("TransactionToken")
             or request_args.get("TransID") or "")
    if not token:
        return False
    api3g = ET.Element("API3G")
    ET.SubElement(api3g, "CompanyToken").text = config["DPO_COMPANY_TOKEN"]
    ET.SubElement(api3g, "Request").text = "verifyToken"
    ET.SubElement(api3g, "TransactionToken").text = token
    root = _dpo_post(config, api3g)
    if _dpo_text(root, "Result") != "000":   # 000 = paid; 900 = not paid yet; etc.
        return False
    try:
        paid = float(_dpo_text(root, "TransactionAmount") or 0)
    except ValueError:
        paid = 0.0
    # Trust only DPO's verified figures, and only if they match what we asked
    # this user to pay — guards against tampered redirects and replays.
    return (_dpo_text(root, "CompanyRef") == payment["tx_ref"]
            and _dpo_text(root, "TransactionCurrency") == payment["currency"]
            and paid >= float(payment["amount"]))


# ==========================================================================
# Flutterwave — JSON API
# ==========================================================================
FLW_API = "https://api.flutterwave.com/v3"


def _flw_headers(config):
    return {"Authorization": f"Bearer {config['FLW_SECRET_KEY']}",
            "Content-Type": "application/json"}


def _flw_create(config, *, tx_ref, amount, currency, customer_email,
                customer_name, redirect_url):
    payload = {
        "tx_ref": tx_ref,
        "amount": str(amount),
        "currency": currency,
        "redirect_url": redirect_url,
        "payment_options": "card",
        "customer": {"email": customer_email, "name": customer_name or customer_email},
        "customizations": {"title": "AutoApply BW subscription"},
    }
    try:
        resp = requests.post(f"{FLW_API}/payments", json=payload,
                             headers=_flw_headers(config), timeout=TIMEOUT)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentError(f"Could not reach the payment provider: {exc}") from exc
    if resp.status_code >= 400 or data.get("status") != "success":
        raise PaymentError(data.get("message") or "Payment initialisation failed.")
    link = (data.get("data") or {}).get("link")
    if not link:
        raise PaymentError("Payment provider did not return a checkout link.")
    return link


def _flw_verify(config, payment, request_args):
    flw_tx_id = (request_args.get("transaction_id") or request_args.get("id")
                 or payment["flw_tx_id"])
    if not flw_tx_id:
        return False
    try:
        resp = requests.get(f"{FLW_API}/transactions/{flw_tx_id}/verify",
                            headers=_flw_headers(config), timeout=TIMEOUT)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentError(f"Could not verify the payment: {exc}") from exc
    if data.get("status") != "success":
        return False
    d = data.get("data") or {}
    try:
        paid = float(d.get("amount") or 0)
    except (TypeError, ValueError):
        paid = 0.0
    return (d.get("status") == "successful" and d.get("tx_ref") == payment["tx_ref"]
            and d.get("currency") == payment["currency"] and paid >= float(payment["amount"]))

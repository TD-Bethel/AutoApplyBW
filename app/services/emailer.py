"""Send a job application email over SMTP.

Each user sends from their OWN email account (configured in Settings). This is
deliberate: blasting many companies from one shared mailbox gets flagged as spam
and blacklisted. Per-user, personalised, rate-limited sending is both safer and
more effective.
"""
import re
import smtplib
from email.message import EmailMessage

from ..security import valid_email


class EmailError(Exception):
    pass


def _header_safe(value):
    """Strip CR/LF so user-supplied text can never inject extra headers."""
    return re.sub(r"[\r\n]+", " ", value or "").strip()


def resolve_sender(user, app_config):
    """Return (host, port, username, password, from_name, from_addr) or raise."""
    host = user["smtp_host"] or app_config.get("SMTP_HOST", "")
    port = user["smtp_port"] or app_config.get("SMTP_PORT", 587)
    username = user["smtp_user"] or app_config.get("SMTP_USER", "")
    password = user["smtp_password"] or app_config.get("SMTP_PASSWORD", "")
    from_name = user["from_name"] or app_config.get("SMTP_FROM_NAME", "") or user["full_name"]
    from_addr = username
    if not (host and username and password):
        raise EmailError(
            "Email is not configured. Go to Settings and add your email SMTP details "
            "(host, address, and app password)."
        )
    return host, int(port), username, password, from_name, from_addr


def _deliver(host, port, username, password, msg):
    """Open an SMTP connection (SSL or STARTTLS), authenticate, and send."""
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls()
                server.ehlo()
        with server:
            server.login(username, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailError(
            "Email login failed. For Gmail/Outlook you usually need an app password, "
            "not your normal password."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise EmailError(f"Could not send email: {exc}") from exc


def send_system_email(app_config, to_email, subject, body):
    """Send a transactional email FROM the platform's own mailbox (the system
    SMTP config, e.g. smtp.gmail.com) — used for password resets and notices.

    Distinct from send_application(), which sends from each user's own inbox.
    """
    host = app_config.get("SMTP_HOST", "")
    port = int(app_config.get("SMTP_PORT", 587) or 587)
    username = app_config.get("SMTP_USER", "")
    password = app_config.get("SMTP_PASSWORD", "")
    from_name = app_config.get("SMTP_FROM_NAME", "") or "AutoApply BW"
    if not (host and username and password):
        raise EmailError(
            "System email is not configured. Set SMTP_HOST / SMTP_USER / "
            "SMTP_PASSWORD in .env (Gmail: smtp.gmail.com, port 587, app password)."
        )
    to_email = _header_safe(to_email)
    if not valid_email(to_email):
        raise EmailError(f"'{to_email}' is not a valid recipient.")
    msg = EmailMessage()
    msg["Subject"] = _header_safe(subject)
    msg["From"] = f"{_header_safe(from_name)} <{username}>"
    msg["To"] = to_email
    msg.set_content(body)
    _deliver(host, port, username, password, msg)


def send_application(user, app_config, to_email, subject, body,
                     attachment_name=None, attachment_bytes=None):
    host, port, username, password, from_name, from_addr = resolve_sender(user, app_config)

    to_email = _header_safe(to_email)
    if not valid_email(to_email):
        raise EmailError(f"'{to_email}' is not a valid recipient email address.")
    subject = _header_safe(subject)
    from_name = _header_safe(from_name).replace('"', "")
    from_addr = _header_safe(from_addr)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
    msg["To"] = to_email
    msg.set_content(body)

    if attachment_bytes:
        filename = attachment_name or "cv.pdf"
        subtype = "pdf" if filename.lower().endswith(".pdf") else "octet-stream"
        msg.add_attachment(
            attachment_bytes,
            maintype="application",
            subtype=subtype,
            filename=filename,
        )

    _deliver(host, port, username, password, msg)

"""Send a job application email over SMTP.

Each user sends from their OWN email account (configured in Settings). This is
deliberate: blasting many companies from one shared mailbox gets flagged as spam
and blacklisted. Per-user, personalised, rate-limited sending is both safer and
more effective.
"""
import smtplib
from email.message import EmailMessage


class EmailError(Exception):
    pass


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


def send_application(user, app_config, to_email, subject, body,
                     attachment_name=None, attachment_bytes=None):
    host, port, username, password, from_name, from_addr = resolve_sender(user, app_config)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
    msg["To"] = to_email
    msg.set_content(body)

    if attachment_bytes:
        msg.add_attachment(
            attachment_bytes,
            maintype="application",
            subtype="octet-stream",
            filename=attachment_name or "cv.txt",
        )

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.ehlo()
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

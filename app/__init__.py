"""Application factory for AutoApply BW."""
import os
import secrets
from datetime import timedelta

from flask import Flask
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

from . import db
from .security import csrf_protect, csrf_token, csrf_exempt


def _int_env(name, default):
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def create_app():
    load_dotenv()
    app = Flask(__name__, instance_relative_config=False)

    secret_key = os.getenv("SECRET_KEY", "").strip()
    if not secret_key or secret_key in ("dev-insecure-change-me",
                                        "please-change-this-to-a-long-random-string"):
        # Never run with a known/guessable secret. A random one keeps the app
        # safe (sessions just reset on restart) until SECRET_KEY is set.
        secret_key = secrets.token_hex(32)
        app.logger.warning(
            "SECRET_KEY is not set in .env — using a temporary random key. "
            "Logins will not survive a restart until you set it."
        )

    app.config.update(
        SECRET_KEY=secret_key,
        DATABASE_PATH=os.getenv("DATABASE_PATH", "instance/autoapply.db"),
        ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        ANTHROPIC_MODEL=os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8").strip(),
        DEEPSEEK_API_KEY=os.getenv("DEEPSEEK_API_KEY", "").strip(),
        DEEPSEEK_MODEL=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip(),
        # Any OpenAI-compatible endpoint works here (DeepSeek, a local
        # FreeLLMAPI aggregator, LM Studio, ...).
        DEEPSEEK_API_URL=os.getenv("DEEPSEEK_API_URL",
                                   "https://api.deepseek.com/chat/completions").strip(),
        SMTP_HOST=os.getenv("SMTP_HOST", "").strip(),
        SMTP_PORT=_int_env("SMTP_PORT", 587),
        SMTP_USER=os.getenv("SMTP_USER", "").strip(),
        SMTP_PASSWORD=os.getenv("SMTP_PASSWORD", ""),
        SMTP_FROM_NAME=os.getenv("SMTP_FROM_NAME", "AutoApply BW"),
        SEND_RATE_PER_HOUR=_int_env("SEND_RATE_PER_HOUR", 30),
        # Card payments (Flutterwave hosted checkout). Leave the keys blank to
        # disable card billing — the billing page then shows a "not available"
        # notice and admin manual activation remains the only path.
        FLW_SECRET_KEY=os.getenv("FLW_SECRET_KEY", "").strip(),
        FLW_PUBLIC_KEY=os.getenv("FLW_PUBLIC_KEY", "").strip(),
        FLW_WEBHOOK_HASH=os.getenv("FLW_WEBHOOK_HASH", "").strip(),
        SUBSCRIPTION_PRICE_BWP=os.getenv("SUBSCRIPTION_PRICE_BWP", "50").strip(),
        SUBSCRIPTION_CURRENCY=os.getenv("SUBSCRIPTION_CURRENCY", "BWP").strip(),
        SUBSCRIPTION_DAYS=_int_env("SUBSCRIPTION_DAYS", 30),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # 10 MB upload cap
        # Session-cookie hardening
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    )

    app.teardown_appcontext(db.close_db)

    # CSRF: every state-changing request must carry the session token.
    app.before_request(csrf_protect)
    app.context_processor(lambda: {"csrf_token": csrf_token})

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; script-src 'self'; frame-ancestors 'none'",
        )
        return resp

    # Blueprints
    from .auth import bp as auth_bp
    from .main import bp as main_bp
    from .admin import bp as admin_bp
    from .assistants import bp as assistants_bp
    from .support import bp as support_bp
    from .billing import bp as billing_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(assistants_bp)
    app.register_blueprint(support_bp)
    app.register_blueprint(billing_bp)

    # The payment provider's webhook proves itself with a signed header, not a
    # session CSRF token, so it must be exempt from the global CSRF check.
    csrf_exempt("billing.webhook")

    with app.app_context():
        db.init_db()
        _ensure_admin(app)

    return app


def _ensure_admin(app):
    """Create the owner/admin account on first run from .env values."""
    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD", "")
    if not email or not password:
        return
    existing = db.query("SELECT id FROM users WHERE email = ?", (email,), one=True)
    if existing:
        return
    db.execute(
        """INSERT INTO users (email, password_hash, full_name, role, status, created_at)
           VALUES (?, ?, ?, 'admin', 'active', ?)""",
        (email, generate_password_hash(password), "Administrator", db.now_iso()),
    )
    app.logger.info("Created admin account: %s", email)

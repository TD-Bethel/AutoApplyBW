"""Application factory for AutoApply BW."""
import os
from flask import Flask
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

from . import db


def create_app():
    load_dotenv()
    app = Flask(__name__, instance_relative_config=False)

    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-insecure-change-me"),
        DATABASE_PATH=os.getenv("DATABASE_PATH", "instance/autoapply.db"),
        ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        ANTHROPIC_MODEL=os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8").strip(),
        SMTP_HOST=os.getenv("SMTP_HOST", "").strip(),
        SMTP_PORT=int(os.getenv("SMTP_PORT", "587") or 587),
        SMTP_USER=os.getenv("SMTP_USER", "").strip(),
        SMTP_PASSWORD=os.getenv("SMTP_PASSWORD", ""),
        SMTP_FROM_NAME=os.getenv("SMTP_FROM_NAME", "AutoApply BW"),
        SEND_RATE_PER_HOUR=int(os.getenv("SEND_RATE_PER_HOUR", "30") or 30),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # 10 MB upload cap
    )

    app.teardown_appcontext(db.close_db)

    # Blueprints
    from .auth import bp as auth_bp
    from .main import bp as main_bp
    from .admin import bp as admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)

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

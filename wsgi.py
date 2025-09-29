# wsgi.py — define and expose the Flask app (no imports from flask_app here!)
import os
from flask import Flask
from models import db  # make sure nfl-picks/models.py exists and defines `db = SQLAlchemy()`

# Optional: trust proxy headers on Heroku (so request.url, scheme, host are correct)
try:
    from werkzeug.middleware.proxy_fix import ProxyFix  # type: ignore
except Exception:
    ProxyFix = None


def create_app() -> Flask:
    app = Flask(__name__)

    # ----------------------------
    # Core config (adjust as needed)
    # ----------------------------
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

    # Heroku provides DATABASE_URL; SQLAlchemy prefers "postgresql://"
    db_url = os.environ.get("DATABASE_URL")
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    # Fallback to a local SQLite file if no DATABASE_URL is set (useful for dev)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url or "sqlite:///local.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Initialize SQLAlchemy
    db.init_app(app)

    # If you're behind a proxy (Heroku), fix request scheme/host
    if ProxyFix is not None:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    # ----------------------------
    # Register blueprints/routes (optional)
    # ----------------------------
    # If you have a routes module exposing `bp = Blueprint(...)`, register it here.
    # This is optional and safely ignored if you don't have such a module.
    try:
        from routes import bp as routes_bp  # noqa: E402
        app.register_blueprint(routes_bp)
    except Exception:
        # No routes blueprint found (or not needed) — that's fine.
        pass

    return app


# Expose a module-level app for Gunicorn: "web: gunicorn wsgi:app"
app = create_app()

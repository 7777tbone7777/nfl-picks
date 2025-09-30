# flask_app.py
import os
from flask import Flask
from models import db  # the shared SQLAlchemy() from models.py

def create_app() -> Flask:
    app = Flask(__name__)

    # Prefer SQLALCHEMY_DATABASE_URI; fall back to DATABASE_URL (Heroku)
    db_uri = os.getenv("SQLALCHEMY_DATABASE_URI") or os.getenv("DATABASE_URL")
    if not db_uri:
        raise RuntimeError(
            "No database URL set. Define SQLALCHEMY_DATABASE_URI or DATABASE_URL in config vars."
        )

    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Optional: honor LOG_LEVEL env var for Flask’s logger
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    try:
        app.logger.setLevel(log_level)
    except Exception:
        pass

    # Bind the shared models.db to this Flask app
    db.init_app(app)

    # If you expose any HTTP routes (optional), define/blueprint them here.
    @app.get("/healthz")
    def healthz():
        return {"ok": True}, 200

    return app


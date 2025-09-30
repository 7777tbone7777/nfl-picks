# flask_app.py
import os
from flask import Flask, jsonify
from models import db

def _normalize_db_url(url: str | None) -> str:
    if not url:
        raise RuntimeError("No DATABASE_URL / SQLALCHEMY_DATABASE_URI set")
    # Heroku used to provide postgres://; SQLAlchemy wants postgresql://
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://") :]
    return url

def create_app() -> Flask:
    app = Flask(__name__)

    raw_uri = os.getenv("SQLALCHEMY_DATABASE_URI") or os.getenv("DATABASE_URL")
    app.config["SQLALCHEMY_DATABASE_URI"] = _normalize_db_url(raw_uri)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # safer across Heroku’s ephemeral networking
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }

    db.init_app(app)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok"), 200

    return app


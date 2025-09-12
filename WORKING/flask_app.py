# flask_app.py
import os
from flask import Flask
from models import db

def create_app():
    app = Flask(__name__)

    db_url = os.getenv("DATABASE_URL", "sqlite:///nfl_picks.db")
    # Heroku old-style URLs:
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    # TODO: register blueprints/routes here when you add them
    return app


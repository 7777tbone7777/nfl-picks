# flask_app.py — tiny adapter so cron code can call create_app()
from wsgi import app  # imports the top-level app defined in wsgi.py


def create_app():
    return app

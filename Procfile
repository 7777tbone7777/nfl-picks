web: gunicorn wsgi:app
worker: python -c "from jobs import run_telegram_listener; run_telegram_listener()"


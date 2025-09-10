import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from flask import Flask, render_template, request, redirect, url_for, abort, jsonify

from models import db, Participant, Week, Game, Pick  # Reminder model optional; not required here


# ---------------------------
# Helpers / Jinja filters
# ---------------------------

def _tz_pst(dt: datetime) -> datetime:
    """Convert aware/naive UTC datetime to America/Los_Angeles for display."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("America/Los_Angeles"))

def jinja_fmt_pst(dt: datetime, fmt: str = "%a %b %d, %I:%M %p %Z") -> str:
    d = _tz_pst(dt)
    return d.strftime(fmt) if d else ""


# ---------------------------
# Telegram routes
# ---------------------------

def register_telegram_routes(app: Flask) -> None:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "devsecret")

    def tg_send(chat_id: str | int, text: str) -> None:
        if not TOKEN:
            app.logger.warning("TELEGRAM_BOT_TOKEN missing; cannot send message")
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
        except Exception as e:
            app.logger.exception("Telegram send failed: %s", e)

    @app.post(f"/telegram/webhook/{SECRET}")
    def telegram_webhook():
        data = request.get_json(silent=True) or {}
        msg = (data.get("message") or {})  # standard message update
        text = (msg.get("text") or "").strip()
        chat = (msg.get("chat") or {})
        chat_id = chat.get("id")
        first_name = chat.get("first_name", "")

        if not chat_id:
            return jsonify({"ok": True, "ignored": "no chat id"}), 200

        # Handle /start <Name>
        if text.startswith("/start"):
            args = text.split(maxsplit=1)
            who = args[1].strip() if len(args) > 1 else first_name or ""
            if not who:
                tg_send(chat_id, "Please resend as: /start YourName")
                return jsonify({"ok": True}), 200

            with app.app_context():
                # case-insensitive lookup by name
                p = Participant.query.filter(Participant.name.ilike(who)).first()
                if not p:
                    # auto-create participant if not found (optional — keeps flow smooth)
                    p = Participant(name=who)
                    db.session.add(p)

                p.telegram_chat_id = str(chat_id)
                db.session.commit()

            tg_send(chat_id, f"Hi {who}! You’re linked. I’ll DM you when picks are open.")
            return jsonify({"ok": True}), 200

        # Basic help
        if text.lower() in ("/help", "help"):
            tg_send(chat_id, "Use /start YourName to link your Telegram to your picks profile.")
            return jsonify({"ok": True}), 200

        # Ignore other noise
        return jsonify({"ok": True}), 200

    @app.get("/admin/invites")
    def admin_invites():
        bot_username = os.getenv("TELEGRAM_BOT_USERNAME")  # e.g., MyBotName (no @)
        secret_hint = os.getenv("TELEGRAM_WEBHOOK_SECRET", "devsecret")
        if not bot_username:
            return (
                "<h1>Telegram Invite Links</h1>"
                "<p><b>TELEGRAM_BOT_USERNAME</b> is not set in Config Vars.</p>",
                200,
            )
        with app.app_context():
            people = Participant.query.order_by(Participant.name.asc()).all()
        links = [
            f"https://t.me/{bot_username}?start={p.name.replace(' ', '%20')}"
            for p in people
        ]
        html = [
            "<h1>Telegram Invite Links</h1>",
            f"<p>Webhook path secret: <code>{secret_hint}</code></p>",
            "<ul>",
        ]
        for p, link in zip(people, links):
            html.append(f"<li>{p.name}: <a href='{link}' target='_blank'>{link}</a></li>")
        html.append("</ul>")
        return "\n".join(html), 200


# ---------------------------
# Core routes
# ---------------------------

def register_core_routes(app: Flask) -> None:
    @app.get("/")
    def index():
        # Show latest season/week we have, and quick links to participants
        latest = (
            db.session.query(Week)
            .order_by(Week.season_year.desc(), Week.week_number.desc())
            .first()
        )
        participants = Participant.query.order_by(Participant.name.asc()).all()
        return render_template("index.html", latest=latest, participants=participants)

    @app.get("/picks/<int:week>/<name>")
    def picks_form(week: int, name: str):
        # Use most recent season for that week
        w = (
            Week.query.filter_by(week_number=week)
            .order_by(Week.season_year.desc())
            .first()
        )
        if not w:
            abort(404, f"Week {week} not found.")

        games = Game.query.filter_by(week_id=w.id).order_by(Game.game_time.asc()).all()
        participant = Participant.query.filter(Participant.name.ilike(name)).first()
        # current picks map
        picks_map = {}
        if participant:
            existing = Pick.query.filter_by(participant_id=participant.id).all()
            for p in existing:
                picks_map[p.game_id] = p.pick  # "home" / "away"

        return render_template(
            "picks_form.html",
            week=w,
            games=games,
            name=name,
            participant=participant,
            picks_map=picks_map,
        )

    @app.post("/picks/<int:week>/<name>")
    def save_picks(week: int, name: str):
        w = (
            Week.query.filter_by(week_number=week)
            .order_by(Week.season_year.desc())
            .first()
        )
        if not w:
            abort(404, f"Week {week} not found.")

        participant = Participant.query.filter(Participant.name.ilike(name)).first()
        if not participant:
            # create on the fly to keep flow easy
            participant = Participant(name=name)
            db.session.add(participant)
            db.session.commit()

        games = Game.query.filter_by(week_id=w.id).all()
        for g in games:
            choice = request.form.get(f"pick_{g.id}")  # "home", "away" or None
            if choice not in ("home", "away"):
                # skip if no selection
                continue
            # upsert Pick
            pk = Pick.query.filter_by(participant_id=participant.id, game_id=g.id).first()
            if not pk:
                pk = Pick(participant_id=participant.id, game_id=g.id, pick=choice)
                db.session.add(pk)
            else:
                pk.pick = choice
        db.session.commit()
        return redirect(url_for("picks_form", week=week, name=name))

    @app.get("/results/<int:week>")
    def results(week: int):
        w = (
            Week.query.filter_by(week_number=week)
            .order_by(Week.season_year.desc())
            .first()
        )
        if not w:
            abort(404, f"Week {week} not found.")

        games = Game.query.filter_by(week_id=w.id).order_by(Game.game_time.asc()).all()
        # very simple results view; template can iterate and compute scoring client-side or
        # you can add server-side scoring later.
        return render_template("results.html", week=w, games=games)

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, time=datetime.now(timezone.utc).isoformat())


# ---------------------------
# App factory
# ---------------------------

def create_app() -> Flask:
    app = Flask(__name__)

    # Database config (Heroku sets DATABASE_URL)
    db_url = os.getenv("DATABASE_URL", "")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)

    app.config.update(
        SQLALCHEMY_DATABASE_URI=db_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SECRET_KEY=os.getenv("SECRET_KEY", "dev"),
    )

    # Init DB
    db.init_app(app)
    with app.app_context():
        db.create_all()

    # Jinja filter for PST display
    app.add_template_filter(jinja_fmt_pst, name="fmt_pst")

    # Register routes
    register_core_routes(app)
    register_telegram_routes(app)

    return app


# For gunicorn: `web: gunicorn 'app:create_app()'`
app = create_app()


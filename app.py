# app.py
import os
from datetime import datetime
from typing import Optional, List, Dict

from flask import Flask, jsonify, render_template, request
from jinja2 import TemplateNotFound

from models import db, Week, Game, Participant


def _coerce_db_url(url: Optional[str]) -> Optional[str]:
    """Heroku still hands out postgres://; SQLAlchemy expects postgresql://"""
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def create_app() -> Flask:
    app = Flask(__name__)

    # ---- Config ----
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev")
    app.config["SQLALCHEMY_DATABASE_URI"] = _coerce_db_url(os.getenv("DATABASE_URL"))
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # ---- DB ----
    db.init_app(app)

    # -------- Helpers --------
    def _week_or_404(season: int, week_num: int):
        w = Week.query.filter_by(season_year=season, week_number=week_num).first()
        if not w:
            return None, jsonify(error=f"Week {week_num} not found for season {season}"), 404
        return w, None, None

    def _games_json(games: List[Game]) -> List[Dict]:
        out: List[Dict] = []
        for g in games:
            out.append(
                {
                    "id": g.id,
                    "away_team": g.away_team,
                    "home_team": g.home_team,
                    "game_time": g.game_time.isoformat() if isinstance(g.game_time, datetime) else str(g.game_time),
                    "status": g.status,
                    "espn_game_id": g.espn_game_id,
                }
            )
        return out

    # -------- Routes --------
    @app.route("/")
    def index():
        # Use your existing template if present; otherwise keep the simple text
        try:
            return render_template("index.html")
        except TemplateNotFound:
            return "Welcome to the NFL Picks App!"

    # JSON: /games?season=2025&week=2  (week defaults to 2 if not provided)
    @app.route("/games")
    def games_query():
        season = request.args.get("season", default=2025, type=int)
        week_num = request.args.get("week", default=2, type=int)
        w, resp, code = _week_or_404(season, week_num)
        if resp:
            return resp, code
        games = Game.query.filter_by(week_id=w.id).order_by(Game.game_time.asc()).all()
        return jsonify(_games_json(games))

    # JSON: /games/<int:week_num>?season=2025
    @app.route("/games/<int:week_num>")
    def games_for_week(week_num: int):
        season = request.args.get("season", default=2025, type=int)
        w, resp, code = _week_or_404(season, week_num)
        if resp:
            return resp, code
        games = Game.query.filter_by(week_id=w.id).order_by(Game.game_time.asc()).all()
        return jsonify(_games_json(games))

    # Admin progress summary you already used
    # /admin/status/<int:week_num>?season=2025
    @app.route("/admin/status/<int:week_num>")
    def admin_status(week_num: int):
        season = request.args.get("season", default=2025, type=int)
        w, resp, code = _week_or_404(season, week_num)
        if resp:
            return resp, code

        games = Game.query.filter_by(week_id=w.id).all()
        total_games = len(games)

        participants = []
        for p in Participant.query.order_by(Participant.name.asc()).all():
            # Try to count picks if a Pick model exists; otherwise report 0
            picks_made = 0
            try:
                from models import Pick  # type: ignore
                picks_made = (
                    db.session.query(Pick)
                    .join(Game, Game.id == Pick.game_id)
                    .filter(Pick.participant_id == p.id, Game.week_id == w.id)
                    .count()
                )
            except Exception:
                picks_made = 0

            participants.append(
                {
                    "name": p.name,
                    "picks_made": picks_made,
                    "total_games": total_games,
                    "complete": picks_made >= total_games and total_games > 0,
                }
            )

        return jsonify(
            {
                "season_year": season,
                "week_number": week_num,
                "total_games": total_games,
                "participants": participants,
            }
        )

    # HTML picks page (falls back to JSON if template is missing)
    # /picks/week/<int:week_num>/<participant_name>?season=2025
    @app.route("/picks/week/<int:week_num>/<participant_name>")
    def picks_form(week_num: int, participant_name: str):
        season = request.args.get("season", default=2025, type=int)
        w, resp, code = _week_or_404(season, week_num)
        if resp:
            return resp, code

        p = Participant.query.filter_by(name=participant_name).first()
        if not p:
            return jsonify(error=f"Participant '{participant_name}' not found"), 404

        games = (
            Game.query.filter_by(week_id=w.id)
            .order_by(Game.game_time.asc())
            .all()
        )

        try:
            return render_template(
                "picks_form.html",
                season=season,
                week=w,
                participant=p,
                games=games,
            )
        except TemplateNotFound:
            # Safe fallback so you never get a 500 here
            return jsonify(
                {
                    "season": season,
                    "week": week_num,
                    "participant": participant_name,
                    "deadline_utc": w.picks_deadline,
                    "games": _games_json(games),
                }
            )

    # Simple health check
    @app.route("/health")
    def health():
        return jsonify(ok=True)

    return app


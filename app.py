# app.py
import os
from datetime import datetime
from flask import Flask, jsonify, request
from models import db, Week, Game

# Default season used when one isn't provided in the URL
DEFAULT_SEASON = int(os.getenv("DEFAULT_SEASON", "2025"))


def _normalize_database_url(url: str) -> str:
    # Heroku still sets postgres://; SQLAlchemy expects postgresql://
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def create_app() -> Flask:
    app = Flask(__name__)

    # --- Database config ---
    db_url = _normalize_database_url(os.environ.get("DATABASE_URL", "sqlite:///app.db"))
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    # --------- Routes ---------

    @app.route("/")
    def index():
        # Keep the same simple landing text you already have
        return "Welcome to the NFL Picks App!"

    @app.route("/healthz")
    def healthz():
        return jsonify(ok=True)

    @app.route("/admin/status/<int:week_number>")
    def admin_status(week_number: int):
        """
        Returns simple JSON about pick progress for a given week.
        (Kept to preserve your working endpoint.)
        """
        season = request.args.get("season", type=int) or DEFAULT_SEASON
        w = Week.query.filter_by(season_year=season, week_number=week_number).first()
        if not w:
            return jsonify(error=f"Week {week_number} for season {season} not found."), 404

        total_games = Game.query.filter_by(week_id=w.id).count()

        # Try to include participants / picks if those models exist
        participants_payload = []
        try:
            from models import Participant, Pick  # optional; if present we compute real counts

            participants = Participant.query.order_by(Participant.name.asc()).all()
            for p in participants:
                picks_made = (
                    Pick.query.join(Game, Game.id == Pick.game_id)
                    .filter(Pick.participant_id == p.id, Game.week_id == w.id)
                    .count()
                )
                participants_payload.append(
                    {
                        "name": p.name,
                        "picks_made": picks_made,
                        "total_games": total_games,
                        "complete": total_games > 0 and picks_made >= total_games,
                    }
                )
        except Exception:
            # If Participant/Pick model isn't present, fall back to an empty list
            participants_payload = []

        return jsonify(
            {
                "week_number": week_number,
                "season_year": season,
                "total_games": total_games,
                "participants": participants_payload,
            }
        )

    @app.route("/games/<int:week_number>")
    def games_api(week_number: int):
        """
        NEW: read-only JSON of the scheduled games for a given week.
        Optional query param: ?season=YYYY  (defaults to DEFAULT_SEASON)
        """
        season = request.args.get("season", type=int) or DEFAULT_SEASON
        w = Week.query.filter_by(season_year=season, week_number=week_number).first()
        if not w:
            return jsonify(error=f"Week {week_number} for season {season} not found."), 404

        games = (
            Game.query.filter_by(week_id=w.id)
            .order_by(Game.game_time.asc())
            .all()
        )

        def _iso(dt: datetime | None) -> str | None:
            if dt is None:
                return None
            # Stored as UTC-naive; return RFC3339-like with 'Z'
            return dt.isoformat() + "Z"

        payload = [
            {
                "away_team": g.away_team,
                "home_team": g.home_team,
                "game_time": _iso(g.game_time),
                "status": g.status,
                "espn_game_id": g.espn_game_id,
            }
            for g in games
        ]
        return jsonify(payload)

    return app


# Allow `python app.py` locally if you want to test without Gunicorn
if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)


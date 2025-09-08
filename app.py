# app.py
import os
from datetime import datetime
from flask import Flask, jsonify, request, render_template
from sqlalchemy import func

from models import db, Week, Game, Participant

# Optional Pick model (if present). We won't fail if it isn't.
try:
    from models import Pick  # type: ignore
    HAS_PICK = True
except Exception:  # ImportError or schema without Pick
    HAS_PICK = False

DEFAULT_SEASON = int(os.environ.get("DEFAULT_SEASON", "2025"))


def create_app() -> Flask:
    app = Flask(__name__)

    # Heroku style DATABASE_URL fixup
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["JSON_SORT_KEYS"] = False

    db.init_app(app)

    @app.route("/")
    def index():
        return "Welcome to the NFL Picks App!"

    # ---- Helpers -----------------------------------------------------------
    def _get_week(season: int, week: int) -> Week | None:
        return Week.query.filter_by(season_year=season, week_number=week).first()

    def _serialize_game(g: Game) -> dict:
        return {
            "id": g.id,
            "week_id": g.week_id,
            "home_team": g.home_team,
            "away_team": g.away_team,
            "game_time": (g.game_time.isoformat() if isinstance(g.game_time, datetime) else g.game_time),
            "home_score": g.home_score,
            "away_score": g.away_score,
            "status": g.status,
            "espn_game_id": g.espn_game_id,
        }

    # ---- JSON: games -------------------------------------------------------
    # GET /games?season=2025&week=2
    @app.route("/games")
    def games_qs():
        season = int(request.args.get("season", DEFAULT_SEASON))
        week = request.args.get("week", type=int)
        if not week:
            # If no week is given, try the most recent week present for that season.
            wk = (
                db.session.query(Week)
                .filter(Week.season_year == season)
                .order_by(Week.week_number.desc())
                .first()
            )
            if not wk:
                return jsonify({"games": [], "season": season, "week": None})
            week = wk.week_number

        wk = _get_week(season, week)
        if not wk:
            return jsonify({"games": [], "season": season, "week": week})

        games = Game.query.filter_by(week_id=wk.id).order_by(Game.game_time.asc()).all()
        return jsonify({"season": season, "week": week, "games": [_serialize_game(g) for g in games]})

    # (Optional alternate path) GET /games/<int:week>?season=2025
    @app.route("/games/<int:week>")
    def games_param(week: int):
        season = int(request.args.get("season", DEFAULT_SEASON))
        wk = _get_week(season, week)
        if not wk:
            return jsonify({"games": [], "season": season, "week": week})
        games = Game.query.filter_by(week_id=wk.id).order_by(Game.game_time.asc()).all()
        return jsonify({"season": season, "week": week, "games": [_serialize_game(g) for g in games]})

    # ---- JSON: simple admin status ----------------------------------------
    # GET /admin/status/2?season=2025
    @app.route("/admin/status/<int:week>")
    def admin_status(week: int):
        season = int(request.args.get("season", DEFAULT_SEASON))
        wk = _get_week(season, week)
        if not wk:
            return jsonify({"week_number": week, "season_year": season, "participants": [], "total_games": 0})

        total_games = Game.query.filter_by(week_id=wk.id).count()

        rows = []
        participants = Participant.query.order_by(Participant.name.asc()).all()
        for p in participants:
            picks_made = 0
            if HAS_PICK:
                try:
                    picks_made = db.session.query(Pick).filter_by(participant_id=p.id).join(
                        Game, Game.id == Pick.game_id
                    ).filter(Game.week_id == wk.id).count()
                except Exception:
                    picks_made = 0
            rows.append(
                {"name": p.name, "picks_made": picks_made, "total_games": total_games, "complete": picks_made >= total_games}
            )

        return jsonify({"week_number": week, "season_year": season, "total_games": total_games, "participants": rows})

    # ---- HTML: picks form (read-only rendering) ---------------------------
    # You said the template you have is `picks_form.html`. We’ll render that.
    # Both of these work:
    #   /picks/2/Tony?season=2025
    #   /picks/week/2/Tony?season=2025
    def _render_picks_form(week: int, name: str):
        season = int(request.args.get("season", DEFAULT_SEASON))

        wk = _get_week(season, week)
        if not wk:
            return f"No data for season {season} week {week}.", 404

        # case-insensitive participant lookup
        participant = (
            Participant.query.filter(func.lower(Participant.name) == func.lower(name)).first()
        )
        if not participant:
            return f"Participant '{name}' not found.", 404

        games = Game.query.filter_by(week_id=wk.id).order_by(Game.game_time.asc()).all()

        # Pre-fill existing picks if your schema/model has them
        existing = {}
        if HAS_PICK:
            try:
                picks = (
                    db.session.query(Pick)
                    .filter_by(participant_id=participant.id)
                    .join(Game, Game.id == Pick.game_id)
                    .filter(Game.week_id == wk.id)
                    .all()
                )
                # Try to infer structure: assume attributes game_id and choice ('home'/'away' or team name)
                for pk in picks:
                    choice = getattr(pk, "choice", None) or getattr(pk, "team", None)
                    existing[getattr(pk, "game_id")] = choice
            except Exception:
                existing = {}

        # Render your existing template
        # (Template variables are generic so they won’t collide with your markup.)
        return render_template(
            "picks_form.html",
            season=season,
            week=week,
            participant_name=participant.name,
            deadline=getattr(wk, "picks_deadline", None),
            games=games,
            existing_picks=existing,
        )

    @app.route("/picks/<int:week>/<string:name>")
    def picks_short(week: int, name: str):
        return _render_picks_form(week, name)

    @app.route("/picks/week/<int:week>/<string:name>")
    def picks_long(week: int, name: str):
        return _render_picks_form(week, name)

    return app


# For local dev: `python app.py`
if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


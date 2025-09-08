# app.py
import os
from flask import Flask, jsonify, render_template, request, abort
from models import db, Week, Game, Participant

def create_app():
    app = Flask(__name__, template_folder="templates")

    # DB config (Heroku sets DATABASE_URL)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/health")
    def health():
        return "ok", 200

    # --- JSON: games for a week ---
    @app.route("/games")
    def games_json():
        week = request.args.get("week", type=int)
        if not week:
            abort(400, description="Missing ?week=<int>")
        season = request.args.get("season", type=int)
        if not season:
            # infer the latest season that has this week
            season = db.session.query(db.func.max(Week.season_year))\
                               .filter_by(week_number=week).scalar()
        w = Week.query.filter_by(season_year=season, week_number=week).first_or_404()
        items = []
        for g in Game.query.filter_by(week_id=w.id).order_by(Game.game_time.asc()).all():
            items.append({
                "away_team": g.away_team,
                "home_team": g.home_team,
                "game_time": g.game_time.isoformat() if g.game_time else None,
                "status": g.status,
                "espn_game_id": g.espn_game_id,
            })
        return jsonify(items)

    # --- JSON: simple admin status for a week ---
    @app.route("/admin/status/<int:week>")
    def admin_status(week: int):
        season = request.args.get("season", type=int)
        if not season:
            season = db.session.query(db.func.max(Week.season_year))\
                               .filter_by(week_number=week).scalar()
        w = Week.query.filter_by(season_year=season, week_number=week).first_or_404()
        total_games = Game.query.filter_by(week_id=w.id).count()
        participants = Participant.query.order_by(Participant.name.asc()).all()
        payload = {
            "week_number": w.week_number,
            "season_year": w.season_year,
            "total_games": total_games,
            "participants": [
                {"name": p.name, "picks_made": 0, "total_games": total_games, "complete": False}
                for p in participants
            ],
        }
        return jsonify(payload)

    # --- HTML: picks page (view-only for now) ---
    # Accept BOTH /picks/week/2/Tony and /picks/Tony/week/2 for convenience.
    @app.route("/picks/week/<int:week>/<string:name>")
    @app.route("/picks/<string:name>/week/<int:week>")
    def picks_form(name: str, week: int):
        season = request.args.get("season", type=int)
        if not season:
            season = db.session.query(db.func.max(Week.season_year))\
                               .filter_by(week_number=week).scalar()
        w = Week.query.filter_by(season_year=season, week_number=week).first()
        if not w:
            abort(404, description="Week not found.")
        games = Game.query.filter_by(week_id=w.id).order_by(Game.game_time.asc()).all()
        participant = Participant.query.filter_by(name=name).first()
        return render_template(
            "picks_form.html",
            name=name,
            participant=participant,
            week=w,
            games=games,
        )

    return app

# Gunicorn entrypoint
app = create_app()


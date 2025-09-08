import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Flask, render_template, request, jsonify, abort
from models import db, Participant, Week, Game, Pick  # assumes these exist

# ----- Config -----
DEFAULT_SEASON = int(os.environ.get("DEFAULT_SEASON", "2025"))
PT = ZoneInfo("America/Los_Angeles")


def _fix_heroku_db_url(url: str | None) -> str | None:
    if not url:
        return url
    # Heroku still hands out postgres://; SQLAlchemy 2.x wants postgresql://
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def create_app():
    app = Flask(__name__)

    # Database
    database_url = _fix_heroku_db_url(os.environ.get("DATABASE_URL"))
    if not database_url:
        # Local dev fallback (never used on Heroku)
        database_url = "postgresql://localhost/nfl_picks"
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    # ---------- Jinja Filters ----------
    def to_pacific(dt):
        """
        Render a stored UTC datetime as Pacific Time like:
        'Thu Sep 11, 5:15 PM PT'
        Works whether dt is naive (assumed UTC) or UTC-aware.
        """
        if dt is None:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_pt = dt.astimezone(PT)
        # e.g. Thu Sep 11, 5:15 PM PT
        return dt_pt.strftime("%a %b %-d, %-I:%M %p PT")

    app.jinja_env.filters["to_pacific"] = to_pacific

    # ---------- Routes ----------

    @app.route("/")
    def index():
        return render_template("index.html")

    # Simple JSON of games to verify the DB (kept as-is but season param is required/handled)
    @app.route("/games/<int:week>")
    def games_json(week: int):
        season = request.args.get("season", type=int) or DEFAULT_SEASON
        wk = Week.query.filter_by(season_year=season, week_number=week).first_or_404()
        games = (
            Game.query.filter_by(week_id=wk.id)
            .order_by(Game.game_time.asc())
            .all()
        )
        return jsonify(
            [
                {
                    "home_team": g.home_team,
                    "away_team": g.away_team,
                    "game_time": g.game_time.isoformat() if g.game_time else None,
                    "status": g.status,
                    "espn_game_id": g.espn_game_id,
                }
                for g in games
            ]
        )

    # Admin status snapshot
    @app.route("/admin/status/<int:week>")
    def admin_status(week: int):
        season = request.args.get("season", type=int) or DEFAULT_SEASON
        wk = Week.query.filter_by(season_year=season, week_number=week).first_or_404()

        total = Game.query.filter_by(week_id=wk.id).count()
        names = ["Will", "Kevin", "Tony"]
        rows = []
        for nm in names:
            p = Participant.query.filter_by(name=nm).first()
            if not p:
                rows.append({"name": nm, "picks_made": 0, "total_games": total, "complete": False})
                continue
            cnt = (
                db.session.query(Pick)
                .join(Game, Pick.game_id == Game.id)
                .filter(Pick.participant_id == p.id, Game.week_id == wk.id)
                .count()
            )
            rows.append({"name": nm, "picks_made": cnt, "total_games": total, "complete": cnt == total})

        return jsonify(
            {"week_number": wk.week_number, "season_year": wk.season_year, "participants": rows, "total_games": total}
        )

    # Picks form: GET shows the table, POST saves/updates picks.
    @app.route("/picks/week/<int:week>/<name>", methods=["GET", "POST"])
    def picks_week(week: int, name: str):
        season = request.args.get("season", type=int) or DEFAULT_SEASON
        wk = Week.query.filter_by(season_year=season, week_number=week).first_or_404()
        # strictly the requested week only
        games = (
            Game.query.filter_by(week_id=wk.id)
            .order_by(Game.game_time.asc())
            .all()
        )

        # participant optional for viewing; create on save if needed
        participant = Participant.query.filter_by(name=name).first()

        if request.method == "POST":
            # Protect against late submissions
            now_utc = datetime.now(timezone.utc)
            if wk.picks_deadline and now_utc > wk.picks_deadline.replace(tzinfo=timezone.utc):
                abort(400, description="Deadline has passed.")

            # Lazily create participant if they don't exist
            if participant is None:
                participant = Participant(name=name)
                db.session.add(participant)
                db.session.flush()  # get id

            # Expect fields: pick_<game_id> value in {"home","away"}
            saved = 0
            for g in games:
                field = f"pick_{g.id}"
                choice = request.form.get(field)
                if choice not in {"home", "away"}:
                    continue  # not selected

                picked_team = g.home_team if choice == "home" else g.away_team

                # upsert
                pk = Pick.query.filter_by(participant_id=participant.id, game_id=g.id).first()
                if pk:
                    pk.picked_team = picked_team
                else:
                    pk = Pick(participant_id=participant.id, game_id=g.id, picked_team=picked_team)
                    db.session.add(pk)
                saved += 1

            db.session.commit()

            # Send the user right back to GET so refresh doesn’t re-POST
            return (
                render_template(
                    "picks_saved.html",
                    name=name,
                    week=wk,
                    saved_count=saved,
                    total=len(games),
                ),
                200,
            )

        # GET – render the form
        # Preload any existing picks (so radios can be pre-checked)
        existing = {}
        if participant:
            rows = (
                db.session.query(Pick)
                .join(Game, Pick.game_id == Game.id)
                .filter(Pick.participant_id == participant.id, Game.week_id == wk.id)
                .all()
            )
            for r in rows:
                existing[r.game_id] = r.picked_team

        return render_template(
            "picks_form.html",
            name=name,
            week=wk,
            games=games,
            participant=participant,
            existing_picks=existing,
        )

    return app


# Gunicorn entrypoint: `web: gunicorn "app:create_app()"`
app = create_app()


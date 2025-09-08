import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    render_template,
    render_template_string,
    request,
    jsonify,
    abort,
    url_for,
)

# Your models (unchanged)
from models import db, Week, Game, Participant, Pick

# ------- Config you can tweak without touching code elsewhere ----------
DEFAULT_SEASON = 2025
DISPLAY_TZ = os.getenv("DISPLAY_TZ", "America/Los_Angeles")  # PDT/PST
# ----------------------------------------------------------------------


def _coerce_database_url(url: str | None) -> str | None:
    """Heroku sometimes provides postgres://; SQLAlchemy wants postgresql://"""
    if not url:
        return None
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def _ensure_aware_utc(dt: datetime | None) -> datetime | None:
    """Treat naive datetimes as UTC; leave aware ones alone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # --- Basic config ---
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db_url = _coerce_database_url(os.getenv("DATABASE_URL"))
    if db_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = db_url

    # Bind SQLAlchemy
    db.init_app(app)

    # ---------- Jinja filter: localtime (PDT by default) ----------
    @app.template_filter("localtime")
    def jinja_localtime(dt: datetime | None, fmt: str = "%a %b %d %I:%M %p %Z"):
        """Render a datetime in DISPLAY_TZ. Naive is assumed UTC."""
        if dt is None:
            return ""
        aware_utc = _ensure_aware_utc(dt)
        local = aware_utc.astimezone(ZoneInfo(DISPLAY_TZ))
        return local.strftime(fmt)

    # ---------- Helpers ----------
    def _effective_season() -> int:
        try:
            return int(request.args.get("season", DEFAULT_SEASON))
        except Exception:
            return DEFAULT_SEASON

    def _pick_column_name() -> str:
        """
        Return whichever attribute your Pick model uses to store a team string.
        """
        for attr in ("picked_team", "team", "selection", "choice", "team_picked"):
            if hasattr(Pick, attr):
                return attr
        return "picked_team"

    # ==================== ROUTES ====================

    @app.route("/")
    def index():
        try:
            return render_template("index.html")
        except Exception:
            return "Welcome to the NFL Picks App!"

    @app.route("/games/<int:week_num>")
    def games_week(week_num: int):
        season = _effective_season()
        w = Week.query.filter_by(season_year=season, week_number=week_num).first_or_404()
        games = (
            Game.query.filter_by(week_id=w.id)
            .order_by(Game.game_time.asc())
            .all()
        )
        payload = [
            {
                "away_team": g.away_team,
                "home_team": g.home_team,
                "game_time": _ensure_aware_utc(g.game_time).isoformat(),
                "status": g.status,
                "espn_game_id": g.espn_game_id,
            }
            for g in games
        ]
        return jsonify(payload)

    @app.route("/games")
    def games_query():
        season = _effective_season()
        try:
            week_num = int(request.args.get("week", 1))
        except Exception:
            week_num = 1
        w = Week.query.filter_by(season_year=season, week_number=week_num).first_or_404()
        games = (
            Game.query.filter_by(week_id=w.id)
            .order_by(Game.game_time.asc())
            .all()
        )
        payload = [
            {
                "away_team": g.away_team,
                "home_team": g.home_team,
                "game_time": _ensure_aware_utc(g.game_time).isoformat(),
                "status": g.status,
                "espn_game_id": g.espn_game_id,
            }
            for g in games
        ]
        return jsonify(payload)

    @app.route("/admin/status/<int:week_num>")
    def admin_status(week_num: int):
        season = _effective_season()
        w = Week.query.filter_by(season_year=season, week_number=week_num).first_or_404()
        total_games = Game.query.filter_by(week_id=w.id).count()

        rows = []
        for p in Participant.query.order_by(Participant.name.asc()).all():
            picks_made = (
                db.session.query(Pick)
                .join(Game, Pick.game_id == Game.id)
                .filter(Game.week_id == w.id, Pick.participant_id == p.id)
                .count()
            )
            rows.append({
                "name": p.name,
                "picks_made": picks_made,
                "total_games": total_games,
                "complete": picks_made >= total_games and total_games > 0,
            })

        return jsonify({
            "season_year": season,
            "week_number": week_num,
            "total_games": total_games,
            "participants": rows,
        })

    @app.route("/picks/week/<int:week_num>/<name>", methods=["GET", "POST"])
    def picks_form(week_num: int, name: str):
        season = _effective_season()
        w = Week.query.filter_by(season_year=season, week_number=week_num).first_or_404()
        games = (
            Game.query.filter_by(week_id=w.id)
            .order_by(Game.game_time.asc())
            .all()
        )
        participant = Participant.query.filter_by(name=name).first()
        
        if request.method == "GET":
            games_view = []
            tz = ZoneInfo(DISPLAY_TZ)
            for g in games:
                local_time = _ensure_aware_utc(g.game_time).astimezone(tz)
                games_view.append({'g': g, 'local_time': local_time})
            
            deadline_local = _ensure_aware_utc(w.picks_deadline).astimezone(tz) if w.picks_deadline else None
            tz_label = tz.key.split('/')[-1]
            
            return render_template(
                "picks_form.html",
                name=name,
                week=w,
                games_view=games_view,
                deadline_local=deadline_local,
                tz_label=tz_label,
                participant=participant,
                display_tz=DISPLAY_TZ,
            )

        if participant is None:
            participant = Participant(name=name)
            db.session.add(participant)
            db.session.flush()

        pick_attr = _pick_column_name()
        saved = 0
        for g in games:
            picked = request.form.get(f"pick_{g.id}")
            if not picked: continue

            existing = Pick.query.filter_by(participant_id=participant.id, game_id=g.id).first()
            if existing:
                setattr(existing, pick_attr, picked)
            else:
                newp = Pick(participant_id=participant.id, game_id=g.id)
                setattr(newp, pick_attr, picked)
                db.session.add(newp)
            saved += 1
        db.session.commit()

        html = """<h1>Picks saved</h1><p>{{saved}} of {{total}} selections saved for {{name}} — Week {{week}}, {{season}}.</p><p><a href="{{back}}">Back to picks</a></p>"""
        back_url = url_for("picks_form", week_num=week_num, name=name, season=season)
        return render_template_string(html, saved=saved, total=len(games), name=name, week=week_num, season=season, back=back_url)

    # (Other routes like /links/week and /results/week remain the same)

    return app

# Main execution for local dev
if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)

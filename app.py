import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    jsonify,
    render_template,
    render_template_string,
    request,
    redirect,
    url_for,
    abort,
)

from models import db, Week, Game, Participant

# Optional: if Pick exists, we'll import it lazily where needed.


# -----------------------------
# App Factory
# -----------------------------
def create_app():
    app = Flask(__name__)

    # Config
    db_url = os.getenv("DATABASE_URL", "")
    # Heroku sometimes gives old scheme "postgres://"
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    app.config.update(
        SQLALCHEMY_DATABASE_URI=db_url or "sqlite:///local.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SECRET_KEY=os.getenv("SECRET_KEY", "dev"),
    )

    db.init_app(app)

    # Defaults
    DEFAULT_SEASON = int(os.getenv("DEFAULT_SEASON", "2025"))
    DISPLAY_TZ = os.getenv("DISPLAY_TZ", "America/Los_Angeles")

    # -----------------------------
    # Helpers
    # -----------------------------
    def get_season_arg():
        try:
            return int(request.args.get("season", DEFAULT_SEASON))
        except Exception:
            return DEFAULT_SEASON

    def get_week_or_404(season_year: int, week_number: int) -> Week:
        w = Week.query.filter_by(season_year=season_year, week_number=week_number).first()
        if not w:
            abort(404, f"Week {week_number} for season {season_year} not found.")
        return w

    def games_for_week_sorted(week_id: int):
        return (
            Game.query.filter_by(week_id=week_id)
            .order_by(Game.game_time.asc())
            .all()
        )

    def to_local(dt_utc, tz_name: str) -> datetime:
        """
        DB stores naive UTC. Make it UTC-aware then convert to display tz.
        """
        tz_local = ZoneInfo(tz_name)
        if dt_utc is None:
            return None
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=ZoneInfo("UTC"))
        else:
            dt_utc = dt_utc.astimezone(ZoneInfo("UTC"))
        return dt_utc.astimezone(tz_local)

    # -----------------------------
    # Routes
    # -----------------------------
    @app.route("/")
    def index():
        # If you have templates/index.html this will render it, otherwise a tiny default text.
        try:
            return render_template("index.html")
        except Exception:
            return "Welcome to the NFL Picks App!"

    # ---- JSON: Games for a week ----
    @app.get("/games/<int:week_num>")
    def games_json(week_num: int):
        season = get_season_arg()
        w = get_week_or_404(season, week_num)
        games = games_for_week_sorted(w.id)

        def dt_to_iso_z(dt):
            # Keep the existing style: naive stored as UTC -> append 'Z'
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.isoformat() + "Z"
            return dt.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")

        payload = [
            {
                "espn_game_id": g.espn_game_id,
                "home_team": g.home_team,
                "away_team": g.away_team,
                "status": g.status,
                "game_time": dt_to_iso_z(g.game_time),
            }
            for g in games
        ]
        return jsonify(payload)

    # ---- JSON: Admin status for a week ----
    @app.get("/admin/status/<int:week_num>")
    def admin_status(week_num: int):
        season = get_season_arg()
        w = get_week_or_404(season, week_num)
        game_ids = [g.id for g in games_for_week_sorted(w.id)]

        participants = Participant.query.order_by(Participant.name.asc()).all()

        # Count picks per participant for the games in this week (if Pick exists).
        picks_count_by_pid = {}
        try:
            from models import Pick  # type: ignore

            if game_ids:
                # Count rows of Pick where game_id in this week
                rows = (
                    db.session.query(Pick.participant_id, db.func.count(Pick.id))
                    .filter(Pick.game_id.in_(game_ids))
                    .group_by(Pick.participant_id)
                    .all()
                )
                picks_count_by_pid = {pid: int(c) for (pid, c) in rows}
        except Exception:
            # If there's no Pick model yet, leave counts at 0
            picks_count_by_pid = {}

        items = []
        total_games = len(game_ids)
        for p in participants:
            made = picks_count_by_pid.get(p.id, 0)
            items.append(
                {
                    "name": p.name,
                    "picks_made": made,
                    "total_games": total_games,
                    "complete": (made >= total_games and total_games > 0),
                }
            )

        return jsonify(
            {
                "week_number": week_num,
                "season_year": season,
                "participants": items,
                "total_games": total_games,
            }
        )

    # ---- Picks entry (GET shows form, POST saves) ----
    @app.route("/picks/week/<int:week_num>/<name>", methods=["GET", "POST"])
    def picks_form(week_num: int, name: str):
        season = get_season_arg()
        week = get_week_or_404(season, week_num)
        games = games_for_week_sorted(week.id)

        # Participant (we won't fail if they aren't in table, we just show a note)
        participant = Participant.query.filter_by(name=name).first()

        # Field name detection for Pick.choice column, but only used on POST:
        def resolve_pick_choice_attr():
            from models import Pick  # lazy import so app works even if Pick doesn't exist at import time
            cols = set(Pick.__table__.columns.keys())
            for cand in ("selected_team", "team", "pick", "choice", "selection"):
                if cand in cols:
                    return cand
            # Fallback: create an attribute name; setattr will still work if model allows it
            return "selected_team"

        if request.method == "POST":
            # Hard guard against missing participants
            if participant is None:
                participant = Participant(name=name)
                db.session.add(participant)
                db.session.flush()

            choice_attr = None
            try:
                from models import Pick  # type: ignore

                choice_attr = resolve_pick_choice_attr()

                # Deadline guard (display only in PT, but comparison in UTC)
                now_utc = datetime.utcnow()
                if week.picks_deadline and now_utc > week.picks_deadline:
                    return render_template_string(
                        "<h3>Deadline passed</h3><p>Picks are locked for this week.</p>"
                        '<p><a href="{{ url }}">Back to picks</a></p>',
                        url=url_for("picks_form", week_num=week_num, name=name, season=season),
                    )

                for g in games:
                    val = request.form.get(f"pick_{g.id}")  # "home" or "away"
                    if not val:
                        continue
                    team_name = g.home_team if val == "home" else g.away_team

                    existing = (
                        Pick.query.filter_by(participant_id=participant.id, game_id=g.id)
                        .first()
                    )
                    if existing:
                        setattr(existing, choice_attr, team_name)
                    else:
                        obj = Pick(participant_id=participant.id, game_id=g.id)
                        setattr(obj, choice_attr, team_name)
                        db.session.add(obj)

                db.session.commit()

                return render_template_string(
                    "<h1>Picks saved</h1>"
                    f"<p>{len(games)} of {len(games)} selections saved for {name} — "
                    f"Week {week.week_number}, {week.season_year}.</p>"
                    '<p><a href="{{ url }}">Back to picks</a></p>',
                    url=url_for("picks_form", week_num=week_num, name=name, season=season),
                )
            except Exception as e:
                db.session.rollback()
                return render_template_string(
                    "<h1>Internal Server Error</h1>"
                    "<p>There was a problem saving picks.</p>"
                    "<pre>{{ err }}</pre>",
                    err=str(e),
                )

        # GET: Build display rows with Pacific time (or DISPLAY_TZ)
        tz_label = ZoneInfo(DISPLAY_TZ)
        tz_abbrev = datetime.now(tz_label).strftime("%Z")
        games_view = [{"g": g, "local_time": to_local(g.game_time, DISPLAY_TZ)} for g in games]
        deadline_local = to_local(week.picks_deadline, DISPLAY_TZ) if week.picks_deadline else None

        # Render your existing template
        return render_template(
            "picks_form.html",
            week=week,
            name=name,
            participant=participant,
            games_view=games_view,
            deadline_local=deadline_local,
            tz_label=tz_abbrev,
        )

    return app


# For local running: `python app.py`
if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))


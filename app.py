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
        We won't guess wrong and break saving.
        """
        for attr in ("team", "selection", "choice", "picked_team", "team_picked"):
            if hasattr(Pick, attr):
                return attr
        # Fallback to 'team' if none matched (keeps legacy behavior)
        return "team"

    # ==================== ROUTES ====================

    @app.route("/")
    def index():
        # Use your existing index.html if present, otherwise a tiny fallback.
        try:
            return render_template("index.html")
        except Exception:
            return "Welcome to the NFL Picks App!"

    # ---- Read-only JSON: list games for a given week (default: week_num path) ----
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

    # ---- Same, but choose week_number via query param (/games?week=2&season=2025) ----
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

    # ---- Admin status for a week (JSON) ----
    @app.route("/admin/status/<int:week_num>")
    def admin_status(week_num: int):
        season = _effective_season()
        w = Week.query.filter_by(season_year=season, week_number=week_num).first_or_404()
        total_games = Game.query.filter_by(week_id=w.id).count()

        # Count picks per participant
        rows = []
        for p in Participant.query.order_by(Participant.name.asc()).all():
            picks_made = (
                db.session.query(Pick)
                .join(Game, Pick.game_id == Game.id)
                .filter(Game.week_id == w.id, Pick.participant_id == p.id)
                .count()
            )
            rows.append(
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
                "participants": rows,
            }
        )

    # ---- Picks form (GET) + save (POST) ----
    @app.route("/picks/week/<int:week_num>/<name>", methods=["GET", "POST"])
    def picks_form(week_num: int, name: str):
        season = _effective_season()
        w = Week.query.filter_by(season_year=season, week_number=week_num).first_or_404()
        games = (
            Game.query.filter_by(week_id=w.id)
            .order_by(Game.game_time.asc())
            .all()
        )

        # Fetch participant (create if not found, so links work even before pre-seeding)
        participant = Participant.query.filter_by(name=name).first()
        if request.method == "GET":
            return render_template(
                "picks_form.html",
                name=name,
                week=w,
                games=games,
                participant=participant,
                display_tz=DISPLAY_TZ,
            )

        # POST: Save submitted picks
        if participant is None:
            participant = Participant(name=name)
            db.session.add(participant)
            db.session.flush()  # get id

        pick_attr = _pick_column_name()
        saved = 0

        for g in games:
            # each radio group name = f"pick_{game.id}"
            picked = request.form.get(f"pick_{g.id}")
            if not picked:
                continue

            existing = Pick.query.filter_by(
                participant_id=participant.id, game_id=g.id
            ).first()

            if existing:
                setattr(existing, pick_attr, picked)
            else:
                newp = Pick(participant_id=participant.id, game_id=g.id)
                setattr(newp, pick_attr, picked)
                db.session.add(newp)
            saved += 1

        db.session.commit()

        # Simple inline confirmation keeps templates untouched
        html = """
        <h1>Picks saved</h1>
        <p>{{saved}} of {{total}} selections saved for {{name}} — Week {{week}}, {{season}}.</p>
        <p><a href="{{back}}">Back to picks</a></p>
        """
        back_url = url_for("picks_form", week_num=week_num, name=name, season=season)
        return render_template_string(
            html,
            saved=saved,
            total=len(games),
            name=name,
            week=week_num,
            season=season,
            back=back_url,
        )

    # ---- Invite links helper (optional) ----
    @app.route("/links/week/<int:week_num>")
    def links_week(week_num: int):
        season = _effective_season()
        participants = Participant.query.order_by(Participant.name.asc()).all()
        base = request.url_root.rstrip("/")
        rows = []
        for p in participants:
            path = url_for("picks_form", week_num=week_num, name=p.name, season=season)
            rows.append((p.name, f"{base}{path}"))
        html = """
        <h1>Week {{week}} – {{season}} invite links</h1>
        <table border="1" cellpadding="6">
          <tr><th>Name</th><th>URL</th></tr>
          {% for n,u in rows %}
            <tr><td>{{n}}</td><td><a href="{{u}}">{{u}}</a></td></tr>
          {% endfor %}
        </table>
        """
        return render_template_string(html, week=week_num, season=season, rows=rows)

    # ---- Results page with winners + per-person tally (optional) ----
    @app.route("/results/week/<int:week_num>")
    def results_week(week_num: int):
        season = _effective_season()
        w = Week.query.filter_by(season_year=season, week_number=week_num).first_or_404()
        games = (
            Game.query.filter_by(week_id=w.id)
            .order_by(Game.game_time.asc())
            .all()
        )

        # Determine winner for a game if it's final
        def winner_for(g: Game) -> str | None:
            if g.home_score is None or g.away_score is None:
                return None
            status = (g.status or "").strip().lower()
            if status not in ("final", "completed", "post"):
                return None
            return g.home_team if g.home_score > g.away_score else g.away_team

        pick_attr = _pick_column_name()

        participants = Participant.query.order_by(Participant.name.asc()).all()
        pid_to_name = {p.id: p.name for p in participants}
        scores = {p.id: 0 for p in participants}

        rows = []
        from sqlalchemy import and_
        for g in games:
            wteam = winner_for(g)
            picks_for_game = {}
            for p in participants:
                pick = (
                    Pick.query.filter(
                        and_(Pick.participant_id == p.id, Pick.game_id == g.id)
                    )
                    .first()
                )
                team = getattr(pick, pick_attr) if pick is not None else None
                picks_for_game[p.id] = team
                if wteam and team == wteam:
                    scores[p.id] += 1
            rows.append(
                {
                    "kick": g.game_time,
                    "matchup": f"{g.away_team} @ {g.home_team}",
                    "status": g.status,
                    "winner": wteam or "pending",
                    "picks": picks_for_game,
                }
            )

        max_score = max(scores.values()) if scores else 0
        leaders = [pid_to_name[pid] for pid, sc in scores.items() if sc == max_score]

        html = """
        <h1>Results — Week {{week}} ({{season}})</h1>
        <p><b>Leaders:</b> {{ leaders|join(", ") }} — {{ max_score }} correct</p>

        <table border="1" cellpadding="6" cellspacing="0">
          <thead>
            <tr>
              <th>Kickoff ({{tz}})</th>
              <th>Matchup</th>
              <th>Status</th>
              <th>Winner</th>
              {% for p in participants %}
                <th>{{ p.name }}</th>
              {% endfor %}
            </tr>
          </thead>
          <tbody>
            {% for r in rows %}
              <tr>
                <td>{{ r.kick|localtime }}</td>
                <td>{{ r.matchup }}</td>
                <td>{{ r.status }}</td>
                <td>{{ r.winner }}</td>
                {% for p in participants %}
                  {% set team = r.picks[p.id] %}
                  <td>{% if team %}{{ team }}{% else %}<em>—</em>{% endif %}</td>
                {% endfor %}
              </tr>
            {% endfor %}
          </tbody>
        </table>

        <h3>Totals</h3>
        <ul>
          {% for p in participants %}
            <li>{{ p.name }}: {{ scores[p.id] }}</li>
          {% endfor %}
        </ul>
        """
        return render_template_string(
            html,
            week=week_num,
            season=season,
            rows=rows,
            participants=participants,
            scores=scores,
            max_score=max_score,
            leaders=leaders,
            tz=ZoneInfo(DISPLAY_TZ).key,
        )

    return app


# For local dev: `python app.py`
if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)


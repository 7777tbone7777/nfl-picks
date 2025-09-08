import os
from datetime import datetime

from flask import Flask, jsonify, render_template, request
from sqlalchemy import func

# Your model layer
# (db is the SQLAlchemy() instance created in models.py)
from models import db, Week, Game, Participant


# ----------------------------
# App factory
# ----------------------------
def create_app() -> Flask:
    app = Flask(__name__)

    # Heroku supplies DATABASE_URL which can be "postgres://"
    database_url = os.getenv("DATABASE_URL", "sqlite:///local.db")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")

    db.init_app(app)

    # ----------------------------
    # Helpers
    # ----------------------------
    def _find_week(week_number: int, season_year: int | None):
        """
        Get Week by (week_number, season_year). If season_year is None,
        pick the most recent season that has that week.
        """
        q = Week.query.filter_by(week_number=week_number)
        if season_year:
            q = q.filter_by(season_year=season_year)
        else:
            # fallback: latest season that has this week
            sub = (
                db.session.query(func.max(Week.season_year))
                .filter(Week.week_number == week_number)
                .scalar_subquery()
            )
            q = q.filter(Week.season_year == sub)
        return q.first_or_404()

    # ----------------------------
    # Routes
    # ----------------------------
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/healthz")
    def healthz():
        return "ok", 200

    # JSON: quick dashboard of picks activity by participant for a week
    # Example: /admin/status/2?season=2025
    @app.route("/admin/status/<int:week_number>")
    def admin_status(week_number: int):
        season_year = request.args.get("season", type=int)
        week = _find_week(week_number, season_year)

        total_games = Game.query.filter_by(week_id=week.id).count()
        participants = Participant.query.order_by(Participant.name.asc()).all()

        summary = []
        picks_count_supported = False
        picks_error = None

        # Try to count real picks if a Pick model exists and we can find a winner column.
        try:
            from models import Pick  # type: ignore

            pick_cols = {c.name for c in Pick.__table__.columns}
            # We just need existence; exact winner column isn't necessary for counting rows
            picks_count_supported = True

            for p in participants:
                count = (
                    db.session.query(Pick)
                    .join(Game, Game.id == Pick.game_id)
                    .filter(Game.week_id == week.id, Pick.participant_id == p.id)
                    .count()
                )
                summary.append(
                    {
                        "name": p.name,
                        "picks_made": count,
                        "total_games": total_games,
                        "complete": count >= total_games and total_games > 0,
                    }
                )
        except Exception as e:  # noqa: BLE001
            picks_error = str(e)
            # Fallback with zero counts
            for p in participants:
                summary.append(
                    {
                        "name": p.name,
                        "picks_made": 0,
                        "total_games": total_games,
                        "complete": False,
                    }
                )

        payload = {
            "week_number": week.week_number,
            "season_year": week.season_year,
            "total_games": total_games,
            "participants": summary,
        }
        if picks_error:
            payload["note"] = f"Pick counting disabled: {picks_error}"

        return jsonify(payload)

    # JSON: list games for a week (ordered by kickoff)
    # Example: /games/2?season=2025
    @app.route("/games/<int:week_number>")
    def games_json(week_number: int):
        season_year = request.args.get("season", type=int)
        week = _find_week(week_number, season_year)

        games = (
            Game.query.filter_by(week_id=week.id)
            .order_by(Game.game_time.asc())
            .all()
        )

        def _fmt(dt: datetime | None):
            return dt.isoformat() if dt else None

        data = [
            {
                "away_team": g.away_team,
                "home_team": g.home_team,
                "game_time": _fmt(g.game_time),
                "status": g.status,
                "espn_game_id": g.espn_game_id,
            }
            for g in games
        ]
        return jsonify(data)

    # Picks page (GET = view, POST = save if possible)
    # Example: /picks/week/2/Tony?season=2025
    @app.route(
        "/picks/week/<int:week_number>/<string:participant_name>",
        methods=["GET", "POST"],
    )
    def picks_week(week_number: int, participant_name: str):
        season_year = request.args.get("season", type=int)
        week = _find_week(week_number, season_year)

        games = (
            Game.query.filter_by(week_id=week.id)
            .order_by(Game.game_time.asc())
            .all()
        )

        participant = Participant.query.filter_by(name=participant_name).first()

        save_result = None
        if request.method == "POST":
            # Create a participant on the fly if not present (handy for quick trials)
            if participant is None:
                participant = Participant(name=participant_name)
                db.session.add(participant)
                db.session.flush()

            # Collect selections
            chosen = []
            for g in games:
                choice = request.form.get(f"pick_{g.id}")
                if choice not in ("home", "away"):
                    continue
                winner = g.home_team if choice == "home" else g.away_team
                chosen.append({"game": g, "winner": winner})

            if not chosen:
                save_result = {"message": "No selections were made.", "preview": []}
            else:
                saved_rows = 0
                preview_rows = []
                try:
                    from models import Pick  # type: ignore

                    pick_cols = {c.name for c in Pick.__table__.columns}
                    # Try to detect a sensible column to hold the winner string
                    winner_field = None
                    for candidate in (
                        "selected_team",
                        "winner",
                        "choice_team",
                        "team_pick",
                        "team",
                        "selection",
                        "pick",
                    ):
                        if candidate in pick_cols:
                            winner_field = candidate
                            break

                    for item in chosen:
                        g = item["game"]
                        wteam = item["winner"]

                        rec = Pick.query.filter_by(
                            participant_id=participant.id,
                            game_id=g.id,
                        ).first()

                        if rec is None:
                            kwargs = dict(participant_id=participant.id, game_id=g.id)
                            if winner_field:
                                kwargs[winner_field] = wteam
                            rec = Pick(**kwargs)
                            db.session.add(rec)
                        else:
                            if winner_field:
                                setattr(rec, winner_field, wteam)

                        saved_rows += 1
                        preview_rows.append(
                            {
                                "home": g.home_team,
                                "away": g.away_team,
                                "winner": wteam,
                            }
                        )

                    db.session.commit()
                    if winner_field:
                        save_result = {
                            "message": f"Saved {saved_rows} picks.",
                            "preview": preview_rows,
                        }
                    else:
                        save_result = {
                            "message": f"Recorded {saved_rows} rows, but no winner column was detected in Pick table (showing preview only).",
                            "preview": preview_rows,
                        }

                except Exception as e:  # noqa: BLE001
                    # If Pick model/structure doesn't match, don't blow up; show preview only.
                    preview_rows = [
                        {
                            "home": it["game"].home_team,
                            "away": it["game"].away_team,
                            "winner": it["winner"],
                        }
                        for it in chosen
                    ]
                    save_result = {
                        "message": f"Could not save to DB (preview only): {e}",
                        "preview": preview_rows,
                    }

        return render_template(
            "picks_form.html",
            week=week,
            games=games,
            participant=participant,
            save_result=save_result,
        )

    return app


# Allow `python app.py` to run a local dev server if you want it.
if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)


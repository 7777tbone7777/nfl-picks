# nfl_data.py
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask_app import create_app
from models import db, Week, Game

ESPN_API = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

def _parse_kickoff(date_str):
    """Return kickoff as aware UTC datetime."""
    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))

def fetch_and_create_week(week_number: int, season_year: int):
    app = create_app()
    with app.app_context():
        url = f"{ESPN_API}?week={week_number}&year={season_year}"
        resp = requests.get(url).json()

        thursday_kick_et = None
        week_obj = Week.query.filter_by(week_number=week_number, season_year=season_year).first()
        if not week_obj:
            # Find Thursday game kickoff
            for event in resp.get("events", []):
                comp = event.get("competitions", [])[0]
                start = _parse_kickoff(event.get("date"))
                if start.astimezone(ZoneInfo("America/New_York")).weekday() == 3:  # Thursday
                    thursday_kick_et = start
                    break

            if not thursday_kick_et and resp.get("events"):
                thursday_kick_et = _parse_kickoff(resp["events"][0]["date"])

            deadline_utc = thursday_kick_et.astimezone(ZoneInfo("UTC"))
            deadline_naive = deadline_utc.replace(tzinfo=None)

            week_obj = Week(
                week_number=week_number,
                season_year=season_year,
                picks_deadline=deadline_naive,
            )
            db.session.add(week_obj)
            db.session.commit()

        for event in resp.get("events", []):
            comp = event.get("competitions", [])[0]
            start = _parse_kickoff(event.get("date"))
            game_time_naive = start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

            home = comp["competitors"][0]
            away = comp["competitors"][1]

            g = Game.query.filter_by(espn_game_id=comp["id"]).first()
            if not g:
                g = Game(
                    week_id=week_obj.id,
                    espn_game_id=comp["id"],
                    home_team=home["team"]["displayName"],
                    away_team=away["team"]["displayName"],
                    game_time=game_time_naive,
                    status="scheduled",
                )
                db.session.add(g)

        db.session.commit()
        print(f"✅ Week {week_number} created/updated")


def update_scores_for_week(week_number: int, season_year: int):
    app = create_app()
    with app.app_context():
        url = f"{ESPN_API}?week={week_number}&year={season_year}"
        resp = requests.get(url).json()

        for event in resp.get("events", []):
            comp = event.get("competitions", [])[0]
            g = Game.query.filter_by(espn_game_id=comp["id"]).first()
            if not g:
                continue

            if comp.get("status", {}).get("type", {}).get("completed"):
                g.status = "final"
                g.home_score = int(comp["competitors"][0]["score"])
                g.away_score = int(comp["competitors"][1]["score"])

                # set winner
                if g.home_score > g.away_score:
                    g.winner = g.home_team
                elif g.away_score > g.home_score:
                    g.winner = g.away_team

        db.session.commit()
        print(f"✅ Week {week_number} scores updated")


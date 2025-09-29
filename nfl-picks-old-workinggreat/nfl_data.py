# nfl_data.py
import sys
from datetime import datetime, timedelta

import requests

from flask_app import create_app
from models import Game, Week, db

# ------------------------
# Helpers
# ------------------------


def current_season_year():
    """
    NFL seasons start in September and run through the Super Bowl in February.
    - If today is Jan–Aug, the season year is the *previous* year.
    - If today is Sep–Dec, the season year is the current year.
    """
    now = datetime.utcnow()
    if now.month < 9:  # Jan–Aug → previous season
        return now.year - 1
    return now.year


def _parse_kickoff(date_str: str) -> datetime:
    # ESPN provides ISO8601 with timezone offsets
    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None)  # store naive UTC


# ------------------------
# Main functions
# ------------------------


def fetch_and_create_week(week_number: int, season_year: int = None):
    """Fetch schedule for a given week/year from ESPN and insert into DB."""
    if season_year is None:
        season_year = current_season_year()

    url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?week={week_number}&year={season_year}"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()

    app = create_app()
    with app.app_context():
        # Create or get week
        week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()
        if not week:
            # Picks deadline = earliest kickoff, usually Thursday night
            first_event = min(data["events"], key=lambda e: e["date"])
            deadline = _parse_kickoff(first_event["date"])
            week = Week(
                week_number=week_number,
                season_year=season_year,
                picks_deadline=deadline,
            )
            db.session.add(week)
            db.session.commit()
            print(f"Created Week {week_number}, {season_year}")

        # Insert games
        for event in data.get("events", []):
            game_id = event["id"]
            comp = event["competitions"][0]
            home_team = comp["competitors"][0]["team"]["displayName"]
            away_team = comp["competitors"][1]["team"]["displayName"]
            kickoff = _parse_kickoff(event["date"])

            game = Game.query.filter_by(espn_game_id=game_id).first()
            if not game:
                game = Game(
                    week_id=week.id,
                    espn_game_id=game_id,
                    home_team=home_team,
                    away_team=away_team,
                    game_time=kickoff,
                    status="scheduled",
                )
                db.session.add(game)

        db.session.commit()
        print(f"Inserted/updated games for Week {week_number}, {season_year}")


def update_scores_for_week(week_number: int, season_year: int = None):
    """Update final scores and winners for a given week/year."""
    if season_year is None:
        season_year = current_season_year()

    url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?week={week_number}&year={season_year}"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()

    app = create_app()
    with app.app_context():
        week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()
        if not week:
            print(f"No week {week_number}, {season_year} found in DB")
            return

        for event in data.get("events", []):
            game_id = event["id"]
            comp = event["competitions"][0]
            competitors = comp["competitors"]

            home = [c for c in competitors if c["homeAway"] == "home"][0]
            away = [c for c in competitors if c["homeAway"] == "away"][0]

            home_team = home["team"]["displayName"]
            away_team = away["team"]["displayName"]
            home_score = int(home.get("score", 0))
            away_score = int(away.get("score", 0))
            status = comp["status"]["type"]["name"].lower()

            game = Game.query.filter_by(espn_game_id=game_id).first()
            if not game:
                continue

            game.home_score = home_score
            game.away_score = away_score
            game.status = status

            if status == "status_final":
                if home_score > away_score:
                    game.winner = home_team
                elif away_score > home_score:
                    game.winner = away_team
                else:
                    game.winner = None  # tie

        db.session.commit()
        print(f"Updated scores for Week {week_number}, {season_year}")


# ------------------------
# CLI entrypoint
# ------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python nfl_data.py fetch_and_create_week <week_number> [season_year]")
        print("  python nfl_data.py update_scores_for_week <week_number> [season_year]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "fetch_and_create_week":
        week = int(sys.argv[2])
        season = int(sys.argv[3]) if len(sys.argv) > 3 else current_season_year()
        fetch_and_create_week(week, season)

    elif command == "update_scores_for_week":
        week = int(sys.argv[2])
        season = int(sys.argv[3]) if len(sys.argv) > 3 else current_season_year()
        update_scores_for_week(week, season)

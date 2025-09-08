import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from app import create_app
from models import db, Week, Game


ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
)


def _parse_kickoff(dt_str: str) -> datetime:
    """
    ESPN returns ISO8601 timestamps in UTC like '2024-09-06T00:15Z'.
    Convert to an aware datetime in UTC.
    """
    # Ensure "+00:00" offset so fromisoformat treats it as aware
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def fetch_and_create_week(week_number: int, season_year: int = 2024) -> None:
    """Fetch schedule from ESPN and create Week + Game rows."""
    app = create_app()
    with app.app_context():
        # Skip if this week already exists
        existing = Week.query.filter_by(
            week_number=week_number,
            season_year=season_year
        ).first()
        if existing:
            print(f"Week {week_number} for {season_year} already exists.")
            return

        print(f"Creating Week {week_number} for season {season_year}...")

        # Fetch scoreboard data from ESPN
        try:
            resp = requests.get(
                ESPN_SCOREBOARD_URL,
                params={"week": week_number, "seasontype": 2, "year": season_year},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"ERROR: Failed to fetch ESPN scoreboard: {e}")
            return

        events = data.get("events", []) or []
        if not events:
            print("ERROR: No events returned by ESPN. Aborting.")
            return

        games_data = []
        thursday_kick_et = None
        earliest_kick_et = None

        for event in events:
            kick_utc = _parse_kickoff(event.get("date"))
            kick_et = kick_utc.astimezone(ZoneInfo("America/New_York"))

            if earliest_kick_et is None or kick_et < earliest_kick_et:
                earliest_kick_et = kick_et

            if kick_et.weekday() == 3:  # 0=Mon ... 3=Thu
                thursday_kick_et = kick_et

            comp = (event.get("competitions") or [{}])[0]
            competitors = comp.get("competitors", [])
            home = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away = next((c for c in competitors if c.get("homeAway") == "away"), None)
            home_team = (home or {}).get("team", {}).get("displayName") or (home or {}).get("team", {}).get("shortDisplayName")
            away_team = (away or {}).get("team", {}).get("displayName") or (away or {}).get("team", {}).get("shortDisplayName")

            if not home_team or not away_team:
                continue

            games_data.append({
                "home_team": home_team,
                "away_team": away_team,
                "game_time": kick_utc,
                "espn_game_id": event.get("id"),
            })

        if not games_data:
            print("ERROR: Could not parse any games from ESPN payload. Aborting.")
            return

        if thursday_kick_et is None:
            print("Warning: No Thursday game found (in ET). Using earliest kickoff as deadline.")
            deadline_utc = (earliest_kick_et or games_data[0]["game_time"].astimezone(ZoneInfo("America/New_York"))).astimezone(ZoneInfo("UTC"))
        else:
            deadline_utc = thursday_kick_et.astimezone(ZoneInfo("UTC"))

        week = Week(week_number=week_number, season_year=season_year, picks_deadline=deadline_utc)
        db.session.add(week)
        db.session.flush()

        for g in games_data:
            db.session.add(Game(
                week_id=week.id,
                home_team=g["home_team"],
                away_team=g["away_team"],
                game_time=g["game_time"],
                espn_game_id=g["espn_game_id"],
            ))

        db.session.commit()
        print(f"Successfully created Week {week_number} with {len(games_data)} games.")


def update_scores_for_week(week_number: int, season_year: int = 2024):
    """
    Fetches the latest scores from ESPN for a given week and updates the database.
    """
    app = create_app()
    with app.app_context():
        week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()
        if not week:
            print(f"Week {week_number} for {season_year} not found.")
            return

        print(f"Updating scores for Week {week_number}...")
        try:
            resp = requests.get(
                ESPN_SCOREBOARD_URL,
                params={"week": week_number, "seasontype": 2, "year": season_year},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"ERROR: Failed to fetch ESPN scoreboard for scores: {e}")
            return
        
        updated_count = 0
        for event in data.get('events', []):
            game = Game.query.filter_by(espn_game_id=event.get('id')).first()
            if game and event.get('status', {}).get('type', {}).get('completed', False):
                comp = (event.get("competitions") or [{}])[0]
                home = next((c for c in comp.get('competitors', []) if c.get('homeAway') == 'home'), None)
                away = next((c for c in comp.get('competitors', []) if c.get('homeAway') == 'away'), None)
                
                if home and away and home.get('score') is not None and away.get('score') is not None:
                    game.home_score = int(home['score'])
                    game.away_score = int(away['score'])
                    game.status = 'final'
                    updated_count += 1

        db.session.commit()
        print(f"Updated scores for {updated_count} final games.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            action = sys.argv[1]
            week_num = int(sys.argv[2])
            season = int(sys.argv[3]) if len(sys.argv) > 3 else 2024
            
            if action == "create":
                fetch_and_create_week(week_num, season)
            elif action == "update_scores":
                update_scores_for_week(week_num, season)
            else:
                print(f"Unknown action: {action}")
        except (ValueError, IndexError):
            print("Usage: python nfl_data.py <create|update_scores> <week_number> [season_year]")
    else:
        print("Usage: python nfl_data.py <create|update_scores> <week_number> [season_year]")

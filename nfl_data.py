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
    """Fetch schedule from ESPN and create Week + Game rows.

    Minimal change from the previous working version, with one important fix:
    The "Thursday" detection now evaluates the kickoff in America/New_York
    instead of UTC. ESPN’s timestamps are UTC, so a 8:15pm ET Thursday game
    appears as ~00:15 Friday UTC. That was causing the "No Thursday game found"
    warning even when a Thursday game exists.
    """
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
            # Kickoff time (aware UTC)
            kick_utc = _parse_kickoff(event.get("date"))
            kick_et = kick_utc.astimezone(ZoneInfo("America/New_York"))

            # Track earliest ET kickoff as a fallback deadline
            if earliest_kick_et is None or kick_et < earliest_kick_et:
                earliest_kick_et = kick_et

            # Detect TRUE Thursday using ET
            if kick_et.weekday() == 3:  # 0=Mon ... 3=Thu
                thursday_kick_et = kick_et

            # Teams
            comp = (event.get("competitions") or [{}])[0]
            competitors = comp.get("competitors", [])

            home = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away = next((c for c in competitors if c.get("homeAway") == "away"), None)

            home_team = (home or {}).get("team", {}).get("displayName") or (home or {}).get("team", {}).get("shortDisplayName")
            away_team = (away or {}).get("team", {}).get("displayName") or (away or {}).get("team", {}).get("shortDisplayName")

            if not home_team or not away_team:
                # Skip malformed event
                continue

            games_data.append(
                {
                    "home_team": home_team,
                    "away_team": away_team,
                    "game_time": kick_utc,  # kept as UTC to match previous behavior
                    "espn_game_id": event.get("id"),
                }
            )

        if not games_data:
            print("ERROR: Could not parse any games from ESPN payload. Aborting.")
            return

        # Picks deadline = Thursday kickoff (ET) if present, else earliest kickoff (ET)
        if thursday_kick_et is None:
            print("Warning: No Thursday game found (in ET). Using earliest kickoff as deadline.")
            deadline_utc = (earliest_kick_et or games_data[0]["game_time"].astimezone(ZoneInfo("America/New_York"))).astimezone(ZoneInfo("UTC"))
        else:
            deadline_utc = thursday_kick_et.astimezone(ZoneInfo("UTC"))

        # Create week
        week = Week(
            week_number=week_number,
            season_year=season_year,
            picks_deadline=deadline_utc,
        )
        db.session.add(week)
        db.session.flush()  # get week.id

        # Create games
        for g in games_data:
            db.session.add(
                Game(
                    week_id=week.id,
                    home_team=g["home_team"],
                    away_team=g["away_team"],
                    game_time=g["game_time"],
                    espn_game_id=g["espn_game_id"],
                )
            )

        db.session.commit()
        print(f"Successfully created Week {week_number} with {len(games_data)} games.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            fetch_and_create_week(int(sys.argv[1]))
        except ValueError:
            print("Please provide a valid week number.")
    else:
        print("Usage: python nfl_data.py <week_number>")


# nfl_data.py
# Create a Week and its Games from ESPN's public NFL scoreboard.
# - Defaults to season 2025
# - Stores kickoff in Game.game_time (UTC, naive)
# - Sets Week.picks_deadline (if column exists) to earliest kickoff

import sys
from datetime import datetime, timezone
from typing import List, Dict

import requests

from app import create_app
from models import db, Week, Game


ESPN_SCOREBOARD = "https://site.api.espn.com/apis/v2/sports/football/nfl/scoreboard"


def _to_utc_naive(iso: str) -> datetime:
    """
    Convert ESPN ISO date (usually with 'Z') to naive UTC datetime.
    """
    # ESPN dates are like "2025-09-12T00:15:00Z"
    if iso.endswith("Z"):
        iso = iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        # Assume UTC if no tzinfo
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def fetch_espn_week(season_year: int, week_number: int) -> List[Dict]:
    """
    Pull a week's schedule from ESPN and return a list of dicts:
      {home_team, away_team, kickoff (UTC naive), espn_game_id}
    """
    params = {"week": week_number, "year": season_year, "seasontype": 2}
    r = requests.get(ESPN_SCOREBOARD, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    games: List[Dict] = []
    for ev in data.get("events", []):
        comps = ev.get("competitions", [])
        if not comps:
            continue
        comp = comps[0]

        # kickoff
        date_iso = comp.get("date") or ev.get("date")
        if not date_iso:
            continue
        kickoff = _to_utc_naive(date_iso)

        # competitors
        home_name = away_name = None
        espn_game_id = ev.get("id")

        for c in comp.get("competitors", []):
            team_obj = c.get("team", {}) or {}
            name = team_obj.get("displayName") or team_obj.get("name") or team_obj.get("shortDisplayName")
            if not name:
                continue
            if c.get("homeAway") == "home":
                home_name = name
            elif c.get("homeAway") == "away":
                away_name = name

        if home_name and away_name:
            games.append(
                {
                    "home_team": home_name,
                    "away_team": away_name,
                    "kickoff": kickoff,
                    "espn_game_id": espn_game_id,
                }
            )

    return games


def fetch_and_create_week(week_number: int, season_year: int = 2025) -> None:
    """
    Create or replace the given week (season_year, week_number) and its games.
    - If the Week exists, its Games are deleted and re-inserted.
    - Week.picks_deadline (if present) is set to the earliest kickoff.
    """
    print(f"Creating Week {week_number} for season {season_year}...")

    app = create_app()
    with app.app_context():
        # Get or create the Week row
        week = (
            Week.query.filter_by(season_year=season_year, week_number=week_number)
            .first()
        )
        if not week:
            week = Week(season_year=season_year, week_number=week_number)
            db.session.add(week)
            db.session.flush()  # ensure week.id is available

        # Fetch schedule
        games = fetch_espn_week(season_year, week_number)

        # Set picks_deadline to earliest kickoff if the column exists
        if games:
            earliest = min(g["kickoff"] for g in games)
            if hasattr(week, "picks_deadline"):
                week.picks_deadline = earliest

        # Replace games
        Game.query.filter_by(week_id=week.id).delete(synchronize_session=False)
        for g in games:
            db.session.add(
                Game(
                    week_id=week.id,
                    home_team=g["home_team"],
                    away_team=g["away_team"],
                    game_time=g["kickoff"],  # UTC naive
                    status="scheduled",
                    espn_game_id=g["espn_game_id"],
                )
            )

        db.session.commit()
        print(f"Successfully created Week {week_number} with {len(games)} games.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python nfl_data.py <week_number> [season_year]")
        sys.exit(1)

    week = int(sys.argv[1])

    # Default season is 2025 unless explicitly provided
    if len(sys.argv) >= 3:
        season = int(sys.argv[2])
    else:
        season = 2025

    fetch_and_create_week(week, season)


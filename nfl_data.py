# nfl_data.py
# ---------------------------------------------------------------------
# Creates Week + Game rows from ESPN's public scoreboard API.
# Works with Flask app factory and your SQLAlchemy models.
# ---------------------------------------------------------------------

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import List, Dict, Optional

import requests

from app import create_app
from models import db, Week, Game

# Change this once per season if you like
DEFAULT_SEASON = 2025

ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/v2/sports/football/nfl/scoreboard"
    "?seasontype=2&week={week}&year={season}"
)


# ---------------------------- ESPN helpers ----------------------------

def _to_naive_utc(dt_iso: str) -> datetime:
    """
    ESPN returns ISO timestamps with 'Z' (UTC). Convert to naive UTC datetime,
    matching your DB's naive 'game_time' column.
    """
    dt = datetime.fromisoformat(dt_iso.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def fetch_week_schedule_from_espn(season: int, week: int) -> List[Dict]:
    """
    Return a list of game dicts: {home, away, game_time, status, espn_id}.
    Raises for network errors or 4xx/5xx. Returns [] if no events present.
    """
    url = ESPN_SCOREBOARD.format(season=season, week=week)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    events = data.get("events") or []
    games: List[Dict] = []

    for ev in events:
        comps = (ev.get("competitions") or [{}])[0]
        competitors = comps.get("competitors") or []
        if len(competitors) < 2:
            continue

        home_name = away_name = None
        for c in competitors:
            team = (c.get("team") or {}).get("displayName") or (c.get("team") or {}).get("shortDisplayName")
            if c.get("homeAway") == "home":
                home_name = team
            else:
                away_name = team

        # Skip if either team missing
        if not home_name or not away_name:
            continue

        dt_iso = comps.get("date") or ev.get("date")
        if not dt_iso:
            continue

        game_time = _to_naive_utc(dt_iso)

        status = ((comps.get("status") or {}).get("type") or {}).get("description") or "STATUS_SCHEDULED"
        espn_id = comps.get("id") or ev.get("id")

        games.append(
            {
                "home": home_name,
                "away": away_name,
                "game_time": game_time,
                "status": status,
                "espn_id": espn_id,
            }
        )

    return games


# --------------------------- DB write helpers -------------------------

def compute_picks_deadline(games: List[Dict]) -> Optional[datetime]:
    """Earliest kickoff (UTC) in the week; None if no games."""
    if not games:
        return None
    return min(g["game_time"] for g in games)


def upsert_week_and_games(season: int, week: int, games: List[Dict]) -> Week:
    """
    Idempotently create Week + its Game rows when missing.
    If the Week already exists, we leave existing rows intact.
    """
    existing = Week.query.filter_by(season_year=season, week_number=week).first()
    if existing:
        print(f"[info] Week {week} {season} already exists "
              f"({Game.query.filter_by(week_id=existing.id).count()} games).")
        return existing

    deadline = compute_picks_deadline(games)
    if deadline is None:
        raise RuntimeError(
            f"No games returned for Week {week} {season}; refusing to create a Week with NULL picks_deadline."
        )

    new_week = Week(
        week_number=week,
        season_year=season,
        picks_deadline=deadline,
        reminder_sent=False,
        created_at=datetime.utcnow(),
    )
    db.session.add(new_week)
    db.session.flush()  # get new_week.id

    for g in games:
        db.session.add(
            Game(
                week_id=new_week.id,
                home_team=g["home"],
                away_team=g["away"],
                game_time=g["game_time"],
                home_score=None,
                away_score=None,
                status=g["status"],
                espn_game_id=str(g["espn_id"]) if g.get("espn_id") is not None else None,
            )
        )

    db.session.commit()
    return new_week


# ------------------------------ Public API ----------------------------

def fetch_and_create_week(week: int, season: Optional[int] = None) -> Optional[Week]:
    """
    Main entry point used by CLI and Heroku run console.
    - Fetch schedule from ESPN
    - Create Week + Game rows if they don't exist
    - Prints friendly diagnostics
    """
    season = int(season or DEFAULT_SEASON)

    print(f"Creating Week {week} for season {season}...")
    try:
        games = fetch_week_schedule_from_espn(season, week)
    except requests.HTTPError as e:
        # Common: ESPN 404s if that week's schedule isn't published yet.
        print(f"[error] ESPN HTTP {e.response.status_code} for week={week}, season={season}.")
        print("        The API may not have published that week yet. Try again later.")
        return None
    except Exception as e:
        print(f"[error] Failed to fetch ESPN data: {e}")
        return None

    if not games:
        print("[warn] ESPN returned 0 games for that week. No DB writes performed.")
        return None

    try:
        wk = upsert_week_and_games(season, week, games)
    except Exception as e:
        db.session.rollback()
        print(f"[error] Database write failed: {e}")
        return None

    count = Game.query.filter_by(week_id=wk.id).count()
    print(f"Successfully created Week {wk.week_number} with {count} games.")
    return wk


# ------------------------------- CLI ---------------------------------

if __name__ == "__main__":
    """
    Usage:
      python nfl_data.py <WEEK> [SEASON]
    Examples:
      python nfl_data.py 2          # uses DEFAULT_SEASON (2025)
      python nfl_data.py 3 2025
    """
    if len(sys.argv) < 2:
        print("Usage: python nfl_data.py <WEEK> [SEASON]")
        sys.exit(1)

    week_arg = int(sys.argv[1])
    season_arg = int(sys.argv[2]) if len(sys.argv) >= 3 else DEFAULT_SEASON

    app = create_app()
    with app.app_context():
        fetch_and_create_week(week_arg, season_arg)


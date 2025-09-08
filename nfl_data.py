# nfl_data.py
# Minimal, surgical update:
# - Default season=2025
# - Compute picks_deadline from earliest kickoff BEFORE inserting week
# - If week exists, update its deadline and replace games
# - Uses Flask app-factory (create_app)

import sys
from datetime import datetime, timezone
from typing import List, Dict, Optional

import requests

from app import create_app
from models import db, Week, Game


# --------------- Utilities ---------------

def _parse_espn_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """
    Parse ESPN ISO datetime strings to a naive UTC datetime (Postgres 'timestamp' compatible).
    Examples ESPN emits: '2025-09-12T00:15Z' or with offset.
    """
    if not dt_str:
        return None
    s = dt_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        # Normalize to UTC & drop tzinfo (store as naive UTC)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _compute_deadline_from_kickoffs(kickoffs: List[Optional[datetime]]) -> datetime:
    """
    Returns the earliest valid kickoff. If none are valid, returns a conservative fallback:
    now (UTC) + 24h, rounded to the next hour.
    """
    valid = [k for k in kickoffs if isinstance(k, datetime)]
    if valid:
        return min(valid)

    # Fallback: 24h from now, on the hour
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    fallback = now_utc.replace(minute=0, second=0, microsecond=0)
    return fallback


# --------------- ESPN Fetch ---------------

def fetch_week_schedule_from_espn(season: int, week: int) -> List[Dict]:
    """
    Fetch weekly schedule from ESPN's public scoreboard.
    Returns list of dicts with: away, home, start (UTC naive datetime), espn_id, status
    """
    url = "https://site.api.espn.com/apis/v2/sports/football/nfl/scoreboard"
    params = {
        "seasontype": 2,  # regular season
        "week": week,
        "year": season,
    }
    resp = requests.get(url, params=params, timeout=25)
    resp.raise_for_status()
    data = resp.json()

    events = data.get("events", []) or []
    out: List[Dict] = []
    for ev in events:
        comps = (ev.get("competitions") or [])
        if not comps:
            continue
        comp = comps[0]

        # Teams
        away_team = None
        home_team = None
        for c in comp.get("competitors", []):
            side = (c.get("homeAway") or "").lower()
            name = (c.get("team") or {}).get("displayName") or (c.get("team") or {}).get("shortDisplayName")
            if side == "home":
                home_team = name
            elif side == "away":
                away_team = name

        # Kickoff
        start_raw = comp.get("date") or ev.get("date")
        start_dt = _parse_espn_datetime(start_raw)

        # Status / ESPN id
        status = ((comp.get("status") or {}).get("type") or {}).get("name") or "SCHEDULED"
        espn_id = ev.get("id")

        if home_team and away_team:
            out.append({
                "home": home_team,
                "away": away_team,
                "start": start_dt,
                "espn_id": espn_id,
                "status": status,
            })

    return out


# --------------- Upsert Week + Games ---------------

def fetch_and_create_week(week: int, season: int = 2025) -> None:
    """
    Create/replace a week's games and ensure weeks.picks_deadline is set.
    Deadline = earliest kickoff of that week (UTC). Safe fallback if none parsed.
    """
    app = create_app()
    with app.app_context():
        print(f"Creating Week {week} for season {season}...")

        schedule = fetch_week_schedule_from_espn(season, week)
        kickoffs = [g["start"] for g in schedule]
        deadline = _compute_deadline_from_kickoffs(kickoffs)

        # Find or create week
        wk = Week.query.filter_by(season_year=season, week_number=week).first()
        if wk is None:
            wk = Week(
                week_number=week,
                season_year=season,
                picks_deadline=deadline,   # ensure NOT NULL
                reminder_sent=False,
            )
            db.session.add(wk)
            db.session.commit()
        else:
            # Update deadline (if changed) and replace games
            wk.picks_deadline = deadline
            db.session.commit()
            Game.query.filter_by(week_id=wk.id).delete(synchronize_session=False)
            db.session.commit()

        # Insert games
        to_add = []
        for g in schedule:
            to_add.append(Game(
                week_id=wk.id,
                home_team=g["home"],
                away_team=g["away"],
                game_time=g["start"],      # stored as naive UTC timestamp
                home_score=None,
                away_score=None,
                status=g["status"],
                espn_game_id=g["espn_id"],
            ))
        if to_add:
            db.session.bulk_save_objects(to_add)
        db.session.commit()

        print(f"Successfully created Week {week} with {len(schedule)} games.")
        print(f"picks_deadline (UTC): {wk.picks_deadline}")


# --------------- CLI ---------------

def _usage() -> None:
    print("Usage:")
    print("  python nfl_data.py <week> [season]")
    print("Examples:")
    print("  python nfl_data.py 3           # week 3, default season 2025")
    print("  python nfl_data.py 10 2025     # week 10, season 2025")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        _usage()
        sys.exit(1)

    try:
        week_arg = int(sys.argv[1])
    except ValueError:
        _usage()
        sys.exit(1)

    if len(sys.argv) >= 3:
        try:
            season_arg = int(sys.argv[2])
        except ValueError:
            _usage()
            sys.exit(1)
    else:
        season_arg = 2025  # default

    fetch_and_create_week(week_arg, season_arg)


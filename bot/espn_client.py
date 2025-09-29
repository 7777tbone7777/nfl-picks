
import logging
from typing import List, Dict, Any, Optional
from .http_utils import get_json_with_retry
from .time_utils import parse_iso_to_aware_utc

log = logging.getLogger("espn_client")
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

async def fetch_week(
    week: int,
    season_year: int,
    timeout_s: float = 20.0,
    retries: int = 3,
    backoff_s: float = 1.5,
) -> List[Dict[str, Any]]:
    params = {"week": week, "year": season_year, "seasontype": 2}
    data = await get_json_with_retry(SCOREBOARD_URL, params=params, timeout_s=timeout_s, retries=retries, backoff_s=backoff_s)
    if not data:
        log.error("No data from ESPN for week=%s season=%s", week, season_year)
        return []

    out: List[Dict[str, Any]] = []
    for ev in (data.get("events") or []):
        try:
            comps = ev.get("competitions", [{}])[0]
            status = (comps.get("status") or {}).get("type") or {}
            state = (status.get("state") or "").lower()  # pre/in/post

            competitors = comps.get("competitors") or []
            away, home = None, None
            for c in competitors:
                side = (c.get("homeAway") or "").lower()
                if side == "away":
                    away = c
                elif side == "home":
                    home = c
            if not home or not away:
                continue

            def _name(x): return (x.get("team") or {}).get("displayName") or (x.get("team") or {}).get("name")
            def _score(x):
                try: return int(x.get("score"))
                except: return None

            away_name = _name(away) or "Away"
            home_name = _name(home) or "Home"
            hs = _score(home)
            a_s = _score(away)

            winner: Optional[str] = None
            if home.get("winner") is True:
                winner = home_name
            elif away.get("winner") is True:
                winner = away_name
            elif hs is not None and a_s is not None and hs != a_s and state == "post":
                winner = home_name if hs > a_s else away_name

            start_utc = parse_iso_to_aware_utc(ev.get("date")) if ev.get("date") else None

            out.append({
                "away_team": away_name,
                "home_team": home_name,
                "away_score": a_s,
                "home_score": hs,
                "state": state,
                "winner": winner,
                "start_utc": start_utc,
                "raw_event_id": ev.get("id"),
            })
        except Exception:
            continue

    if not out:
        log.warning("ESPN returned 0 events for week=%s season=%s", week, season_year)
    return out

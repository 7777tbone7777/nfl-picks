import logging
from typing import Any, Dict, List, Optional, Tuple

from .http_utils import get_json_with_retry
from .time_utils import parse_iso_to_aware_utc

log = logging.getLogger("espn_client")
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


def _get_espn_seasontype_and_week(week: int) -> Tuple[int, int]:
    """
    Determine ESPN seasontype and week number based on our internal week number.
    - Weeks 1-18: Regular season (seasontype=2)
    - Weeks 19+: Playoffs (seasontype=3), ESPN week = week - 18

    Returns: (seasontype, espn_week)
    """
    if week <= 18:
        return (2, week)  # Regular season
    else:
        return (3, week - 18)  # Playoffs: Wild Card=1, Divisional=2, Conf=3, Pro Bowl=4, Super Bowl=5


async def fetch_week(
    week: int,
    season_year: int,
    timeout_s: float = 20.0,
    retries: int = 3,
    backoff_s: float = 1.5,
) -> List[Dict[str, Any]]:
    # Determine ESPN seasontype and week from our internal week number
    seasontype, espn_week = _get_espn_seasontype_and_week(week)
    params = {"week": espn_week, "year": season_year, "seasontype": seasontype}
    data = await get_json_with_retry(
        SCOREBOARD_URL,
        params=params,
        timeout_s=timeout_s,
        retries=retries,
        backoff_s=backoff_s,
    )
    if not data:
        log.error("No data from ESPN for week=%s season=%s", week, season_year)
        return []

    out: List[Dict[str, Any]] = []
    for ev in data.get("events") or []:
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

            def _name(x):
                return (x.get("team") or {}).get("displayName") or (x.get("team") or {}).get("name")

            def _score(x):
                try:
                    return int(x.get("score"))
                except:
                    return None

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

            # Extract spread/odds data
            favorite_team: Optional[str] = None
            spread_pts: Optional[float] = None
            odds_list = comps.get("odds") or []
            if odds_list:
                o = odds_list[0]
                spread_val = o.get("spread")
                home_fav = (o.get("homeTeamOdds") or {}).get("favorite", False)
                away_fav = (o.get("awayTeamOdds") or {}).get("favorite", False)

                if home_fav:
                    favorite_team = home_name
                    spread_pts = abs(float(spread_val)) if spread_val is not None else None
                elif away_fav:
                    favorite_team = away_name
                    spread_pts = abs(float(spread_val)) if spread_val is not None else None

            out.append(
                {
                    "away_team": away_name,
                    "home_team": home_name,
                    "away_score": a_s,
                    "home_score": hs,
                    "state": state,
                    "winner": winner,
                    "start_utc": start_utc,
                    "raw_event_id": ev.get("id"),
                    "favorite_team": favorite_team,
                    "spread_pts": spread_pts,
                }
            )
        except Exception:
            continue

    if not out:
        log.warning("ESPN returned 0 events for week=%s season=%s", week, season_year)
    return out

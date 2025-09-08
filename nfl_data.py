import requests
from datetime import datetime, timezone

def fetch_week_schedule_from_espn(season: int, week: int):
    """
    Try ESPN 'scoreboard' first (with and without year).
    If that 404s, fall back to the core events API.
    Returns a list of dicts: {home, away, date (UTC ISO), espn_id}
    """
    # 1) Scoreboard (with year)
    urls = [
        f"https://site.api.espn.com/apis/v2/sports/football/nfl/scoreboard?seasontype=2&week={week}&year={season}",
        # 2) Scoreboard (without year) – sometimes published earlier than the year-scoped one
        f"https://site.api.espn.com/apis/v2/sports/football/nfl/scoreboard?seasontype=2&week={week}",
    ]

    for url in urls:
        resp = requests.get(url, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            sched = _parse_scoreboard_events(data)
            if sched:
                return sched
        elif resp.status_code not in (404, 400):
            # hard error other than “not found” – bubble it up
            resp.raise_for_status()

    # 3) Fallback: core API (usually available for future weeks)
    return _fetch_from_core_api(season, week)


def _parse_scoreboard_events(payload: dict):
    """Extract schedule from scoreboard JSON into our normalized list."""
    events = payload.get("events") or []
    out = []
    for ev in events:
        espn_id = str(ev.get("id") or "")
        dt = ev.get("date")
        # Normalize date to ISO UTC string
        try:
            kick_utc = (
                datetime.fromisoformat(dt.replace("Z", "+00:00"))
                .astimezone(timezone.utc)
                .isoformat()
            ) if dt else None
        except Exception:
            kick_utc = dt

        comps = (ev.get("competitions") or [{}])[0]
        teams = comps.get("competitors") or []
        home, away = None, None
        for t in teams:
            name = (
                (t.get("team") or {}).get("abbreviation")
                or (t.get("team") or {}).get("displayName")
            )
            side = t.get("homeAway")
            if side == "home":
                home = name
            elif side == "away":
                away = name
        if home and away and kick_utc:
            out.append({"home": home, "away": away, "date": kick_utc, "espn_id": espn_id})
    return out


def _fetch_from_core_api(season: int, week: int):
    """
    Use ESPN core API as a reliable fallback for future weeks.
    We fetch the week’s events list, then pull each event to get teams/date.
    """
    base = f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/{season}/types/2/weeks/{week}/events"
    r = requests.get(base, timeout=20)
    r.raise_for_status()
    data = r.json()
    items = data.get("items") or []
    out = []

    for it in items:
        ev_url = it.get("$ref")
        if not ev_url:
            continue
        er = requests.get(ev_url, timeout=20)
        if er.status_code != 200:
            continue
        ev = er.json()
        espn_id = str(ev.get("id") or "")
        dt = ev.get("date")
        try:
            kick_utc = (
                datetime.fromisoformat(dt.replace("Z", "+00:00"))
                .astimezone(timezone.utc)
                .isoformat()
            ) if dt else None
        except Exception:
            kick_utc = dt

        comps_url = (ev.get("competitions") or {}).get("$ref")
        if not comps_url:
            continue
        cr = requests.get(comps_url, timeout=20)
        if cr.status_code != 200:
            continue
        comps = (cr.json().get("items") or [])
        comp = comps[0] if comps else {}
        comp_ref = comp.get("$ref")
        if not comp_ref:
            continue
        comp_detail = requests.get(comp_ref, timeout=20).json()
        competitors = (comp_detail.get("competitors") or {}).get("items") or []

        home, away = None, None
        for c in competitors:
            cref = c.get("$ref")
            if not cref:
                continue
            cd = requests.get(cref, timeout=20).json()
            side = cd.get("homeAway")
            team_ref = (cd.get("team") or {}).get("$ref")
            team_name = None
            if team_ref:
                td = requests.get(team_ref, timeout=20).json()
                team_name = td.get("abbreviation") or td.get("displayName")
            if side == "home":
                home = team_name
            elif side == "away":
                away = team_name

        if home and away and kick_utc:
            out.append({"home": home, "away": away, "date": kick_utc, "espn_id": espn_id})

    return out


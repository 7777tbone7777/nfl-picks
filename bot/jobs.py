#flake8: noqanimport asyncio
import json
import logging
import urllib.request
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import datetime as _dt
from sqlalchemy import text as T
import httpx
from sqlalchemy import text as _text
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from flask_app import create_app
from models import Game, Participant, Pick, Week, db
import json, re, urllib.request

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jobs")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_USER_IDS", "").replace(" ", "").split(",") if x.isdigit()
}

PT = ZoneInfo("America/Los_Angeles")

# -------- ESPN odds import (isolated helper) ---------------------------------

# Public scoreboard endpoint:
# Example: https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&year=2025&week=7
ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    "?seasontype=2&year={year}&week={week}"
)

def import_odds_upcoming():
    """
    Refresh spreads for the upcoming week from The Odds API
    and write into games.favorite_team / games.spread_pts.
    Safe to run multiple times.
    """
    import os, datetime as dt, requests
    from decimal import Decimal
    from sqlalchemy import text as T
    from flask_app import create_app
    from models import db

    API = os.environ.get("ODDS_API_KEY")
    if not API:
        print("ODDS_API_KEY not set"); return

    app = create_app()                     # <-- create app
    with app.app_context():                # <-- now app is defined
        # detect the next week having a future kickoff
        r = db.session.execute(T("""
            SELECT w.season_year AS season, w.week_number AS week
            FROM games g
            JOIN weeks w ON w.id = g.week_id
            WHERE g.game_time > NOW()
            GROUP BY w.season_year, w.week_number
            ORDER BY MIN(g.game_time) ASC
            LIMIT 1
        """)).mappings().first()
        if not r:
            print("No upcoming week found"); return
        SEASON, WEEK = int(r["season"]), int(r["week"])

        # load games for that week
        games = db.session.execute(T("""
          SELECT g.id, g.away_team, g.home_team
          FROM games g
          JOIN weeks w ON w.id = g.week_id
          WHERE w.season_year=:y AND w.week_number=:w
        """), {"y": SEASON, "w": WEEK}).mappings().all()
        key = {((g["away_team"] or "").strip().casefold(),
                (g["home_team"] or "").strip().casefold()): g["id"] for g in games}

        # fetch OddsAPI
        SPORT="americanfootball_nfl"
        DATE_FROM=(dt.datetime.utcnow()-dt.timedelta(days=3)).strftime("%Y-%m-%dT00:00:00Z")
        DATE_TO  =(dt.datetime.utcnow()+dt.timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z")
        url=f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds"
        params=dict(apiKey=API, regions="us", markets="spreads", oddsFormat="american",
                    dateFormat="iso", commenceTimeFrom=DATE_FROM, commenceTimeTo=DATE_TO)
        ev = requests.get(url, params=params, timeout=30).json()

        BOOK_PRIORITY = ["DraftKings","BetMGM","FanDuel","BetOnline.ag","BetRivers","BetUS","Bovada"]

        def pick_outcomes(bookmakers):
            by = {bm["title"]: bm for bm in bookmakers}
            for name in BOOK_PRIORITY:
                bm = by.get(name)
                if not bm: continue
                for mk in bm.get("markets", []):
                    if mk.get("key") == "spreads" and mk.get("outcomes"):
                        return mk["outcomes"]
            for bm in bookmakers:
                for mk in bm.get("markets", []):
                    if mk.get("key") == "spreads" and mk.get("outcomes"):
                        return mk["outcomes"]
            return None

        updated = 0
        for e in ev:
            away = (e.get("away_team") or "").strip().casefold()
            home = (e.get("home_team") or "").strip().casefold()
            gid = key.get((away, home))
            if not gid:
                continue
            outs = pick_outcomes(e.get("bookmakers", []))
            if not outs:
                continue
            fav = min(outs, key=lambda o: o.get("point", 0))  # most negative = favorite
            fav_name, pts = fav.get("name"), fav.get("point")
            if fav_name is None or pts is None:
                continue
            pts= -abs(Decimal(str(pts)))
            db.session.execute(T("""
              UPDATE games SET favorite_team=:fav, spread_pts=:pts WHERE id=:gid
            """), {"fav": fav_name, "pts": Decimal(str(pts)), "gid": gid})
            updated += 1

        db.session.commit()
        print(f"Updated spreads for week {WEEK}/{SEASON}: {updated}")


def _safe(s):
    return (s or "").strip().lower()

def _parse_odds_from_competition(comp: dict) -> tuple[str | None, float | None]:
    """
    Returns (favorite_team_display_name, spread_float) if odds are present,
    else (None, None).

    ESPN odds shape we rely on:
      comp["odds"][0]["details"]  -> e.g. "PIT -5.5" or "DET -2.5"
      comp["competitors"][i]["team"]["abbreviation"] / ["displayName"]
    """
    odds = (comp.get("odds") or [])
    if not odds:
        return (None, None)

    first = odds[0] or {}
    details = (first.get("details") or "").strip()
    if not details:
        return (None, None)

    # details format like "PIT -5.5". Split on last space to be safe.
    parts = details.rsplit(" ", 1)
    if len(parts) != 2:
        return (None, None)

    fav_abbr, spread_txt = parts[0].strip(), parts[1].strip()
    try:
        spread = float(spread_txt.replace("½", ".5"))
    except Exception:
        return (None, None)

    # map abbreviation -> displayName using the two competitors
    fav_display = None
    for c in comp.get("competitors", []):
        team = c.get("team") or {}
        if _safe(team.get("abbreviation")) == _safe(fav_abbr):
            fav_display = team.get("displayName")
            break

    return (fav_display, spread if fav_display else None)

def import_odds_from_espn(season_year: int, week: int, *, dry_run: bool = False) -> dict:
    """
    Fetch odds for (season_year, week) and update games.favorite_team/spread_pts.

    Matching rule:
      - We build a key "away @ home" using ESPN event competitors' displayName,
        and match against your games rows by lowercased away/home team strings.

    Returns a summary dict with counts.
    """
    # 1) Fetch JSON
    url = ESPN_SCOREBOARD.format(year=season_year, week=week)
    with urllib.request.urlopen(url, timeout=20) as r:
        data = json.load(r)

    events = data.get("events") or []
    if not events:
        return {"status": "no_events", "season_year": season_year, "week": week, "updated": 0, "skipped": 0}

    # 2) Build a lookup from (away, home) -> (fav, spread)
    # ESPN puts competitors in an arbitrary order; we compute who is home.
    matchup_to_odds: dict[tuple[str, str], tuple[str, float]] = {}

    for ev in events:
        comps = ((ev.get("competitions") or [{}])[0]).get("competitors") or []
        if len(comps) < 2:
            continue
        # ESPN marks home with "homeAway": "home" | "away"
        home = next((c for c in comps if _safe(c.get("homeAway")) == "home"), None)
        away = next((c for c in comps if _safe(c.get("homeAway")) == "away"), None)
        if not home or not away:
            continue

        home_name = ((home.get("team") or {}).get("displayName") or "").strip()
        away_name = ((away.get("team") or {}).get("displayName") or "").strip()
        if not home_name or not away_name:
            continue

        fav_name, spread = _parse_odds_from_competition((ev.get("competitions") or [{}])[0])
        if fav_name and spread is not None:
            matchup_to_odds[(away_name.lower(), home_name.lower())] = (fav_name, float(spread))

    # 3) Read your games for that week and apply updates
    updated = 0
    skipped = 0

    rows = db.session.execute(
        T("""
        SELECT g.id, g.away_team, g.home_team, g.favorite_team, g.spread_pts
        FROM games g
        JOIN weeks w ON w.id = g.week_id
        WHERE w.season_year = :y AND w.week_number = :w
        ORDER BY g.id
        """),
        {"y": season_year, "w": week},
    ).mappings().all()

    for g in rows:
        key = (g["away_team"].strip().lower(), g["home_team"].strip().lower())
        if key not in matchup_to_odds:
            skipped += 1
            continue

        fav_name, spread = matchup_to_odds[key]

        # Only write if something changed or was missing
        cur_fav = (g["favorite_team"] or "").strip()
        cur_spr = g["spread_pts"]
        needs = (cur_fav != fav_name) or (cur_spr is None) or (float(cur_spr) != float(spread))

        if not needs:
            skipped += 1
            continue

        if not dry_run:
            db.session.execute(
                T("UPDATE games SET favorite_team=:f, spread_pts=:s WHERE id=:id"),
                {"f": fav_name, "s": spread, "id": g["id"]},
            )
        updated += 1

    if not dry_run and updated:
        db.session.commit()

    return {
        "status": "ok",
        "season_year": season_year,
        "week": week,
        "events": len(events),
        "updated": updated,
        "skipped": skipped,
    }
# -------- end ESPN odds import ----------------------------------------------


def _pt(dt_utc) -> str:
    """
    Format a UTC (or naive-UTC) datetime/string as Pacific Time.
    Example output: 'Thu 10/16 05:15 PM PT'
    """
    if not dt_utc:
        return "TBD"

    # Accept str or datetime
    if isinstance(dt_utc, str):
        # normalize 'Z' or naive
        s = dt_utc.replace("Z", "+00:00")
        try:
            dt_utc = datetime.fromisoformat(s)
        except Exception:
            return dt_utc  # fallback: show raw

    # Treat naive as UTC
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)

    local = dt_utc.astimezone(PT)
    # If you really want “PST” vs “PDT”, keep %Z. If you want a generic “PT”, hardcode it.
    return local.strftime("%a %m/%d %I:%M %p %Z").replace("PDT", "PT").replace("PST", "PT")


# --- ESPN NFL scoreboard (read-only fetch) ---
# Regular season = seasontype=2. Preseason(1), Postseason(3).
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

# --- Import a week from ESPN, including spreads (idempotent) ------------------
def import_week_from_espn(season_year: int, week: int) -> dict:
    """
    Ensure (season_year, week) exists in weeks; upsert all games for that week
    from ESPN into games (home/away/time/status/scores + favorite_team/spread_pts).
    Idempotent.
    """
    from datetime import datetime, timezone
    from sqlalchemy import text as _text

    # If you added my robust fetch, great; otherwise your existing one is fine.
    # It just needs to return dicts possibly containing: favorite_team, spread_pts.
    events = fetch_espn_scoreboard(week, season_year) or []

    def _parse_start(ts: str):
        if not ts:
            return None
        s = ts.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)  # store naive UTC
            return dt
        except Exception:
            return None

    def _coerce_float_or_none(x):
        try:
            return float(x) if x is not None else None
        except Exception:
            return None

    app = create_app()
    with app.app_context():
        # 1) Ensure the (season, week) exists and get week_id
        row = db.session.execute(
            _text("""
                INSERT INTO weeks (season_year, week_number)
                VALUES (:y, :w)
                ON CONFLICT (season_year, week_number) DO NOTHING
                RETURNING id
            """),
            {"y": season_year, "w": week},
        ).first()
        if row:
            week_id = row[0]
        else:
            week_id = db.session.execute(
                _text("SELECT id FROM weeks WHERE season_year=:y AND week_number=:w"),
                {"y": season_year, "w": week},
            ).scalar()

        if not events:
            # Nothing available yet—exit cleanly
            db.session.commit()
            return {
                "season_year": season_year,
                "week": week,
                "events": 0,
                "created": 0,
                "updated": 0,
                "note": "No events returned from ESPN",
            }

        state_to_status = {"pre": "scheduled", "in": "in_progress", "post": "final"}

        created = 0
        updated = 0

        for e in events:
            away = (e.get("away_team") or "").strip()
            home = (e.get("home_team") or "").strip()
            if not away or not home:
                continue  # skip junk rows

            start_dt = _parse_start(e.get("start_time"))
            status = state_to_status.get((e.get("state") or "").lower(), "scheduled")
            home_score = e.get("home_score")
            away_score = e.get("away_score")
            favorite_team = (e.get("favorite_team") or None)
            spread_pts = _coerce_float_or_none(e.get("spread_pts"))

            # 2) Try UPDATE existing row matched by teams within the week
            res = db.session.execute(
                _text("""
                    UPDATE games
                    SET game_time     = COALESCE(:game_time, game_time),
                        status        = COALESCE(:status, status),
                        home_score    = COALESCE(:home_score, home_score),
                        away_score    = COALESCE(:away_score, away_score),
                        favorite_team = COALESCE(:favorite_team, favorite_team),
                        spread_pts    = COALESCE(:spread_pts, spread_pts)
                    WHERE week_id=:week_id
                      AND lower(home_team)=lower(:home)
                      AND lower(away_team)=lower(:away)
                """),
                {
                    "game_time": start_dt,
                    "status": status,
                    "home_score": home_score,
                    "away_score": away_score,
                    "favorite_team": favorite_team,
                    "spread_pts": spread_pts,
                    "week_id": week_id,
                    "home": home,
                    "away": away,
                },
            )

            if res.rowcount == 0:
                # 3) INSERT new game
                db.session.execute(
                    _text("""
                        INSERT INTO games
                            (week_id, home_team, away_team, game_time, status,
                             home_score, away_score, favorite_team, spread_pts)
                        VALUES
                            (:week_id, :home, :away, :game_time, :status,
                             :home_score, :away_score, :favorite_team, :spread_pts)
                    """),
                    {
                        "week_id": week_id,
                        "home": home,
                        "away": away,
                        "game_time": start_dt,
                        "status": status,
                        "home_score": home_score,
                        "away_score": away_score,
                        "favorite_team": favorite_team,
                        "spread_pts": spread_pts,
                    },
                )
                created += 1
            else:
                updated += 1

        db.session.commit()
        return {
            "season_year": season_year,
            "week": week,
            "events": len(events),
            "created": created,
            "updated": updated,
        }


def ats_winners_for_week(week_number: int, season_year: int | None = None):
    """
    Compute Against-The-Spread winners for the given week, then count each participant's
    correct ATS picks. Push games are ignored (neither side gets credit).

    Returns: (counts_dict, winners_by_game, finals_count)
      - counts_dict: {participant_name: int}
      - winners_by_game: {game_id: "TEAM" | "PUSH" | None}
      - finals_count: number of FINAL games considered
    """
    from sqlalchemy import text as T
    app = create_app()
    with app.app_context():
        # Resolve season if not provided
        if season_year is None:
            season_year = db.session.execute(
                T("""
                    SELECT MAX(season_year)
                    FROM weeks
                    WHERE week_number = :w
                """),
                {"w": week_number},
            ).scalar()

        # Pull FINAL games with favorite/spread (may be NULL)
        games = db.session.execute(
            T("""
                SELECT g.id, w.season_year, w.week_number,
                       g.home_team, g.away_team, g.home_score, g.away_score,
                       g.status, g.favorite_team, g.spread_pts
                FROM games g
                JOIN weeks w ON w.id = g.week_id
                WHERE w.season_year = :y
                  AND w.week_number = :w
                  AND lower(coalesce(g.status,'')) = 'final'
                ORDER BY g.id
            """),
            {"y": season_year, "w": week_number},
        ).mappings().all()

        winners_by_game: dict[int, str | None] = {}
        finals_count = 0
        for g in games:
            finals_count += 1
            winners_by_game[g["id"]] = _ats_winner(
                g["home_team"], g["away_team"],
                g["home_score"], g["away_score"],
                g["favorite_team"], g["spread_pts"],
            )

        # Count correct ATS picks (ignore PUSH/None)
        rows = db.session.execute(
            T("""
                SELECT p.participant_id,
                       (SELECT name FROM participants WHERE id = p.participant_id) AS name,
                       p.game_id, p.selected_team
                FROM picks p
                JOIN games g ON g.id = p.game_id
                JOIN weeks w ON w.id = g.week_id
                WHERE w.season_year = :y
                  AND w.week_number = :w
                  AND p.selected_team IS NOT NULL
            """),
            {"y": season_year, "w": week_number},
        ).mappings().all()

        counts: dict[str, int] = {}
        for r in rows:
            ats = winners_by_game.get(r["game_id"])
            if not ats or ats == "PUSH":
                continue  # no credit on push/unknown
            if (r["selected_team"] or "").strip().lower() == ats.strip().lower():
                counts[r["name"]] = counts.get(r["name"], 0) + 1

        return counts, winners_by_game, finals_count

def _ats_winner(home_team: str, away_team: str,
                home_score: int, away_score: int,
                favorite_team: str | None, spread_pts: float | None,) -> str | None:
    """
    Returns the ATS winner team name, or None for a push/unknown.
    favorite_team is the team name (home or away) that is favored by spread_pts.
    spread_pts > 0 means the favorite must win by > spread to cover.
    """
    if home_score is None or away_score is None:
        return None

    if favorite_team and spread_pts is not None:
        spr = float(spread_pts)
        fav = (favorite_team or "").strip().lower()
        h = (home_team or "").strip().lower()
        a = (away_team or "").strip().lower()

        if fav == h:
            diff = (home_score - away_score) - spr   # home favorite
            if diff > 0:  # covered
                return home_team
            if diff < 0:  # underdog covers
                return away_team
            return None   # push
        elif fav == a:
            diff = (away_score - home_score) - spr   # away favorite
            if diff > 0:
                return away_team
            if diff < 0:
                return home_team
            return None

    # No spread/favorite -> straight-up
    if home_score > away_score:
        return home_team
    if away_score > home_score:
        return away_team
    return None


def _get_latest_season_year():
    from sqlalchemy import text as _text

    return db.session.execute(_text("SELECT MAX(season_year) FROM weeks")).scalar()

def _find_last_completed_week_number(season_year: int) -> int | None:
    """
    Return the highest week_number where ALL games are final (completed).
    """
    from sqlalchemy import text as _text
    row = db.session.execute(
        _text("""
            SELECT w.week_number
            FROM weeks w
            JOIN games g ON g.week_id = w.id
            WHERE w.season_year = :y
            GROUP BY w.week_number
            HAVING SUM(CASE WHEN COALESCE(g.status,'') <> 'final' THEN 1 ELSE 0 END) = 0
            ORDER BY w.week_number DESC
            LIMIT 1
        """),
        {"y": season_year},
    ).scalar()
    return int(row) if row is not None else None

def _find_upcoming_week_row(season_year: int, now_naive_utc):
    """
    Return the first week that clearly has a future kickoff time AND at least one game.
    If all game_time values are NULL for the next week, this will return None and the caller
    should fall back to last_completed + 1.
    """
    from sqlalchemy import text as _text

    rows = (
        db.session.execute(
            _text("""
                SELECT w.week_number,
                       MIN(g.game_time) AS first_kick,
                       COUNT(g.id)      AS games
                FROM weeks w
                LEFT JOIN games g ON g.week_id = w.id
                WHERE w.season_year = :y
                GROUP BY w.week_number
                ORDER BY w.week_number
            """),
            {"y": season_year},
        ).mappings().all()
    )

    # IMPORTANT: require at least 1 game AND a real (non-NULL) future kickoff
    for r in rows:
        if r["games"] and r["first_kick"] and r["first_kick"] > now_naive_utc:
            return r
    return None

def _compute_week_results(season_year: int, week: int):
    """
    Returns list of dicts: [{'participant_id':..., 'name':..., 'wins':int}, ...]
    Counts a win when pick matches game winner (based on scores) for FINAL games.
    """
    from sqlalchemy import text as _text

    rows = (
        db.session.execute(
            _text(
                """
        WITH week_games AS (
          SELECT g.id AS game_id,
                 CASE WHEN g.home_score > g.away_score THEN g.home_team
                      WHEN g.away_score > g.home_score THEN g.away_team
                      ELSE NULL END AS winner
          FROM games g
          JOIN weeks w ON w.id = g.week_id
          WHERE w.season_year=:y AND w.week_number=:w AND g.status='final'
        ),
        per_participant AS (
          SELECT p.id AS participant_id,
                 COALESCE(p.display_name, p.name, CONCAT('P', p.id::text)) AS name
          FROM participants p
        ),
        scores AS (
          SELECT pp.participant_id,
                 pp.name,
                 COUNT(*) FILTER (
                   WHERE pk.selected_team IS NOT NULL
                     AND wg.winner IS NOT NULL
                     AND lower(pk.selected_team::text) = lower(wg.winner::text)
                 ) AS wins
          FROM per_participant pp
          LEFT JOIN picks pk ON pk.participant_id = pp.participant_id
          LEFT JOIN week_games wg ON wg.game_id = pk.game_id
          GROUP BY pp.participant_id, pp.name
        )
        SELECT * FROM scores ORDER BY wins DESC, name ASC
    """
            ),
            {"y": season_year, "w": week},
        )
        .mappings()
        .all()
    )
    return [
        {
            "participant_id": r["participant_id"],
            "name": r["name"],
            "wins": int(r["wins"] or 0),
        }
        for r in rows
    ]


def _compute_season_totals(season_year: int, up_to_week_inclusive: int):
    """
    Season totals starting from WEEK 2 (Week 1 treated as zero).
    Returns [{'participant_id', 'name', 'wins'}, ...] ordered by wins desc.
    """
    from sqlalchemy import text as _text

    rows = (
        db.session.execute(
            _text(
                """
        WITH season_games AS (
          SELECT g.id AS game_id,
                 CASE WHEN g.home_score > g.away_score THEN g.home_team
                      WHEN g.away_score > g.home_score THEN g.away_team
                      ELSE NULL END AS winner
          FROM games g
          JOIN weeks w ON w.id = g.week_id
          WHERE w.season_year=:y AND w.week_number >= 2 AND w.week_number <= :wk AND g.status='final'
        ),
        per_participant AS (
          SELECT p.id AS participant_id,
                 COALESCE(p.display_name, p.name, CONCAT('P', p.id::text)) AS name
          FROM participants p
        ),
        scores AS (
          SELECT pp.participant_id,
                 pp.name,
                 COUNT(*) FILTER (
                   WHERE pk.selected_team IS NOT NULL
                     AND sg.winner IS  NOT NULL
                     AND lower(pk.selected_team::text) = lower(sg.winner::text)
                 ) AS wins
          FROM per_participant pp
          LEFT JOIN picks pk ON pk.participant_id = pp.participant_id
          LEFT JOIN season_games sg ON sg.game_id = pk.game_id
          GROUP BY pp.participant_id, pp.name
        )
        SELECT * FROM scores ORDER BY wins DESC, name ASC
    """
            ),
            {"y": season_year, "wk": up_to_week_inclusive},
        )
        .mappings()
        .all()
    )
    return [
        {
            "participant_id": r["participant_id"],
            "name": r["name"],
            "wins": int(r["wins"] or 0),
        }
        for r in rows
    ]


def _format_winners_and_totals(week: int, weekly_rows, season_rows):
    # Weekly winners (could be tie)
    if not weekly_rows:
        weekly_line = f"Week {week} Winner: (no final games / no picks)"
    else:
        top = weekly_rows[0]["wins"]
        winners = [r for r in weekly_rows if r["wins"] == top]
        names = ", ".join(f"{w['name']} ({w['wins']})" for w in winners)
        weekly_line = f"🏆 Week {week} Winner(s): {names}"

    # Season table (compact)
    lines = ["\n📊 Season Standings (through Week " + str(week) + "):"]
    rank = 1
    for r in season_rows:
        lines.append(f"{rank}. {r['name']} — {r['wins']}")
        rank += 1
    return weekly_line + "\n" + "\n".join(lines)

def cron_send_upcoming_week() -> dict:
    """
    Post the matchups for the next week so participants can make picks.

    Guardrails:
      - Runs only on TUESDAY in America/Los_Angeles unless ALLOW_ANYDAY is set
        to one of: 1, true, yes, on (case-insensitive).

    Strategy:
      1) Try strict finder (needs first_kick > now_utc AND games > 0).
      2) If none (e.g., first_kick is NULL), fall back to last_completed + 1.
      3) Ensure the target week exists in DB (import once if empty).
      4) Send using send_week_games(...).
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from sqlalchemy import text as _text
    import os

    app = create_app()
    with app.app_context():
        # --- Tuesday guard (PT) with ALLOW_ANYDAY override ---
        now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
        allow_anyday = os.getenv("ALLOW_ANYDAY", "").strip().lower() in {"1", "true", "yes", "on"}
        if not allow_anyday and now_pt.weekday() != 1:  # Monday=0, Tuesday=1
            try:
                logger.info("cron_send_upcoming_week: skipping (not Tuesday PT). now_pt=%s", now_pt)
            except NameError:
                pass
            return {"ok": False, "reason": "skipped_non_tuesday", "now_pt": now_pt.isoformat()}

        # --- Season & time base ---
        season = db.session.execute(_text("SELECT MAX(season_year) FROM weeks")).scalar()
        if not season:
            return {"ok": False, "reason": "no season"}

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        # --- 1) Try to find an upcoming week with a real future kickoff ---
        wk = _find_upcoming_week_row(season, now_utc)

        if not wk:
            # --- 2) Fallback when MIN(game_time) is NULL or not future ---
            last_completed = _find_last_completed_week_number(season) or 0
            target_week = last_completed + 1
        else:
            target_week = int(wk["week_number"])

        # --- 3) Ensure the week has games; import if necessary (idempotent) ---
        cnt = db.session.execute(
            _text("""
                SELECT COUNT(*) FROM games g
                JOIN weeks w ON w.id = g.week_id
                WHERE w.season_year = :y AND w.week_number = :w
            """),
            {"y": season, "w": target_week},
        ).scalar()

        if not cnt:
            import_week_from_espn(season, target_week)
            cnt = db.session.execute(
                _text("""
                    SELECT COUNT(*) FROM games g
                    JOIN weeks w ON w.id = g.week_id
                    WHERE w.season_year = :y AND w.week_number = :w
                """),
                {"y": season, "w": target_week},
            ).scalar()

        if not cnt:
            return {
                "ok": False,
                "reason": "no upcoming week found after fallback",
                "season_year": int(season),
                "week": int(target_week),
            }

        # --- 4) Send using the same helper your /sendweek command uses ---
        # If your helper is positional (season, week), use: send_week_games(season, target_week)
        res = send_week_games(week_number=target_week, season_year=season)

        return {
            "ok": True,
            "season_year": int(season),
            "week": int(target_week),
            **(res or {}),
        }


def cron_announce_weekly_winners() -> dict:
    """
    Tuesday (America/Los_Angeles) 08:55 PT: announce last week's winners + season totals,
    broadcasted to all participants with telegram_chat_id. De-duped per season/week.
    """
    import os
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    import httpx
    from sqlalchemy import text as _text

    app = create_app()
    with app.app_context():
        # Tuesday guard (PT)
        now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
        if now_pt.weekday() != 1:  # Monday=0, Tuesday=1
            logger.info("cron_announce_weekly_winners: skip (not Tuesday PT) now_pt=%s", now_pt)
            return {"status": "skipped_non_tuesday", "now_pt": now_pt.isoformat()}

        # Determine "last completed" week as (upcoming_week - 1)
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        season = _get_latest_season_year()
        if not season:
            return {"error": "no season_year in weeks"}

        upcoming = _find_upcoming_week_row(season, now_utc_naive)
        if not upcoming:
            # If no upcoming, assume max existing week and announce that-1
            last_completed = _find_last_completed_week_number(season)
            if not last_completed:
                logger.info("cron_announce_weekly_winners: no completed week yet")
                return {"status": "noop", "reason": "no completed week"}
            week_to_announce = max(2, last_completed)
        else:
            week_to_announce = max(2, int(upcoming["week_number"]) - 1)

        # Dedupe table (separate from sendweek)
        db.session.execute(
            _text(
                """
            CREATE TABLE IF NOT EXISTS week_announcements (
                season_year INTEGER NOT NULL,
                week_number INTEGER NOT NULL,
                sent_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (season_year, week_number)
            )
        """
            )
        )
        db.session.commit()

        # Try to claim this announcement first (prevents double sends)
        claimed = db.session.execute(
            _text(
                """
            INSERT INTO week_announcements (season_year, week_number)
            VALUES (:y, :w)
            ON CONFLICT (season_year, week_number) DO NOTHING
            RETURNING 1
        """
            ),
            {"y": season, "w": week_to_announce},
        ).first()
        if not claimed:
            db.session.commit()
            logger.info(
                "cron_announce_weekly_winners: already sent for %s W%s; skipping",
                season,
                week_to_announce,
            )
            return {
                "status": "skipped_duplicate",
                "season_year": season,
                "week": week_to_announce,
            }

        # Compute results
        weekly = _compute_week_results(season, week_to_announce)
        season_totals = _compute_season_totals(season, week_to_announce)
        text_msg = _format_winners_and_totals(week_to_announce, weekly, season_totals)

        # Broadcast
        participants = (
            db.session.execute(
                _text(
                    """
            SELECT id, COALESCE(display_name, name, CONCAT('P', id::text)) AS name, telegram_chat_id
            FROM participants
            WHERE telegram_chat_id IS NOT NULL
        """
                )
            )
            .mappings()
            .all()
        )

        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            logger.warning("cron_announce_weekly_winners: TELEGRAM_BOT_TOKEN not set")
            return {"error": "TELEGRAM_BOT_TOKEN not set"}

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        sent = 0
        with httpx.Client(timeout=20) as client:
            for p in participants:
                try:
                    r = client.post(url, json={"chat_id": p["telegram_chat_id"], "text": text_msg})
                    r.raise_for_status()
                    sent += 1
                    logger.info("📣 Announced to %s", p["name"])
                except Exception:
                    logger.exception("Failed announcing to participant_id=%s", p["id"])

        db.session.commit()
        return {
            "status": "sent",
            "season_year": season,
            "week": week_to_announce,
            "recipients": sent,
            "weekly_top": weekly[0]["wins"] if weekly else 0,
            "participants_ranked": len(season_totals),
        }


def cron_import_upcoming_week() -> dict:
    """
    On Tuesday (America/Los_Angeles), import the NEXT upcoming week (first_kick > now)
    from ESPN into the DB so the 9am sender has data. Safe to run daily (Tue-guarded).
    Honours ALLOW_ANYDAY to enable mid-week testing.
    """
    import os
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from sqlalchemy import text as _text

    app = create_app()
    with app.app_context():
        # Tuesday guard (PT) with ALLOW_ANYDAY override (matches sendweek_upcoming behavior)
        now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
        allow_anyday = os.getenv("ALLOW_ANYDAY", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not allow_anyday and now_pt.weekday() != 1:  # Monday=0, Tuesday=1
            logger.info(
                "cron_import_upcoming_week: skipping (not Tuesday PT). now_pt=%s",
                now_pt,
            )
            return {"status": "skipped_non_tuesday", "now_pt": now_pt.isoformat()}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        season = db.session.execute(_text("SELECT MAX(season_year) FROM weeks")).scalar()
        if not season:
            return {"error": "no season_year in weeks"}

        # Find the next week with kickoff in the future
        rows = (
            db.session.execute(
                _text(
                    """
            SELECT w.week_number, MIN(g.game_time) AS first_kick, COUNT(g.id) AS games
            FROM weeks w
            LEFT JOIN games g ON g.week_id = w.id
            WHERE w.season_year = :y
            GROUP BY w.week_number
            ORDER BY w.week_number
        """
                ),
                {"y": season},
            )
            .mappings()
            .all()
        )

        upcoming = next((r for r in rows if r["first_kick"] and r["first_kick"] > now), None)
        # If no week has future kickoff (or week exists but has 0 games), try to infer by +1
        if not upcoming:
            # fall back to max week in season + 1
            max_week = (
                db.session.execute(
                    _text(
                        """
                SELECT COALESCE(MAX(week_number), 0) FROM weeks WHERE season_year=:y
            """
                    ),
                    {"y": season},
                ).scalar()
                or 0
            )
            target_week = max_week + 1
        else:
            target_week = int(upcoming["week_number"])

        # Import (idempotent)
        result = import_week_from_espn(season, target_week)

        # Recount games for logging
        count_after = db.session.execute(
            _text(
                """
            SELECT COUNT(*) FROM games g
            JOIN weeks w ON w.id = g.week_id
            WHERE w.season_year=:y AND w.week_number=:w
        """
            ),
            {"y": season, "w": target_week},
        ).scalar()

        logger.info(
            "cron_import_upcoming_week: imported Week %s %s, games now=%s",
            target_week,
            season,
            count_after,
        )
        return {
            "status": "imported",
            "season_year": season,
            "week": target_week,
            "games": count_after,
            **result,
        }


def detect_current_context(timeout: float = 15.0):
    """
    Hit ESPN's no-parameter scoreboard and return (year, season_type_int, week_number).
    season_type_int: 1=pre, 2=reg, 3=post
    """
    with httpx.Client(timeout=timeout, headers={"User-Agent": "nfl-picks-bot/1.0"}) as client:
        j = client.get(ESPN_SCOREBOARD_URL).json()
    year = int(j["season"]["year"])
    st = j["season"]["type"]
    if isinstance(st, str):
        st = {"pre": 1, "reg": 2, "post": 3}.get(st.lower(), 2)
    week = int(j["week"]["number"])
    return year, int(st), week

# --- ESPN scoreboard: fetch + (optional) spread parsing ----------------------
def fetch_espn_scoreboard(week: int, season_year: int):
    """
    Returns a list of dicts for the given NFL week from ESPN:
      {
        "home_team": "...",
        "away_team": "...",
        "start_time": "2025-10-19T13:30Z" (ISO if present),
        "state": "pre" | "in" | "post",
        "home_score": 31,
        "away_score": 28,
        "favorite_team": "Jacksonville Jaguars" | None,
        "spread_pts": 3.5 | None,
      }
    """
    ua = {"User-Agent": "Mozilla/5.0 (nfl-picks bot)"}
    def _get(url: str):
        req = urllib.request.Request(url, headers=ua)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)

    # Preferred (the one that worked in your test)
    urls = [
        f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&year={season_year}&week={week}",
        # Legacy fallbacks (some weeks/years used to work here)
        f"https://site.api.espn.com/apis/v2/sports/football/nfl/scoreboard?seasontype=2&year={season_year}&week={week}",
        f"https://www.espn.com/nfl/scoreboard/_/week/{week}/year/{season_year}/seasontype/2",
    ]

    last_err = None
    data = None
    for url in urls:
        try:
            data = _get(url)
            break
        except Exception as e:
            last_err = e

    if data is None:
        raise RuntimeError(f"ESPN fetch failed for week {week} {season_year}: {last_err}")

    events = data.get("events", []) or []
    out = []

    for ev in events:
        comp = (ev.get("competitions") or [{}])[0]

        # Teams & names
        cteams = comp.get("competitors") or []
        if len(cteams) != 2:
            continue
        # ESPN flags home/away on competitors
        home_c = next((t for t in cteams if str(t.get("homeAway")) == "home"), cteams[0])
        away_c = next((t for t in cteams if str(t.get("homeAway")) == "away"), cteams[1])

        def _team_fullname(c):
            t = c.get("team") or {}
            # displayName is usually "Jacksonville Jaguars"
            return (t.get("displayName")
                    or t.get("name")
                    or t.get("location") or "").strip()

        home_team = _team_fullname(home_c)
        away_team = _team_fullname(away_c)

        # Scores (if any)
        def _score(c):
            s = c.get("score")
            try:
                return int(s) if s is not None and s != "" else None
            except Exception:
                return None

        home_score = _score(home_c)
        away_score = _score(away_c)

        # Game state
        status = (ev.get("status") or {}).get("type") or {}
        state = (status.get("state") or "").lower()  # 'pre','in','post'

        # Start time
        start_time = None
        if ev.get("date"):
            # already ISO with Z; keep raw string for your importer to parse
            start_time = ev["date"]

        # Odds / spread / favorite
        favorite_team = None
        spread_pts = None
        odds_list = comp.get("odds") or []
        if odds_list:
            o = odds_list[0]  # usually first is current line
            # Some payloads include structured fields:
            fav_name = o.get("favorite")
            spr_val = o.get("spread")
            details = o.get("details")  # e.g., "PIT -5.5"

            if spr_val is not None:
                try:
                    spread_pts = float(spr_val)
                except Exception:
                    spread_pts = None

            if fav_name:
                # fav_name can be abbreviation or name; try to match by suffix/prefix
                cand = [home_team, away_team]
                # Loose match: if token appears in full name (JAX -> Jacksonville Jaguars)
                fav_low = fav_name.lower()
                mtch = [t for t in cand if fav_low in t.lower() or t.lower().startswith(fav_low)]
                favorite_team = (mtch[0] if mtch else fav_name)

            # Fallback: parse details like "PIT -5.5" / "JAX -2.5"
            if (favorite_team is None or spread_pts is None) and details:
                m = re.search(r"([A-Za-z]{2,4})\s*([+-]?\d+(?:\.\d+)?)", details)
                if m:
                    abbr = m.group(1).lower()
                    try:
                        spread_pts = float(m.group(2))
                    except Exception:
                        pass
                    # Pick the team whose name contains that abbr (rough heuristic)
                    picks = [home_team, away_team]
                    favorite_team = next(
                        (t for t in picks if abbr in t.lower() or t.lower().startswith(abbr)),
                        favorite_team
                    )

        out.append({
            "home_team": home_team,
            "away_team": away_team,
            "home_score": home_score,
            "away_score": away_score,
            "start_time": start_time,
            "state": state,  # 'pre'|'in'|'post'
            "favorite_team": favorite_team,
            "spread_pts": spread_pts,
        })

    return out

def sync_week_scores_from_espn(week: int, season_year: int) -> dict:
    """
    Pull ESPN events for (season_year, week), match to DB games by team names,
    update scores/status (and winner if present), and return a summary.
    Adds `linkable` = # of DB games that had a matching ESPN event.
    Keeps `matched` semantics as "rows changed this run" for backward-compatibility.
    """
    from sqlalchemy import text as _text

    # Fetch ESPN events
    events = fetch_espn_scoreboard(week, season_year)

    # Build a case-insensitive lookup: (away, home) -> event
    es_map = {}
    for e in events:
        a = (e.get("away_team") or "").strip().lower()
        h = (e.get("home_team") or "").strip().lower()
        es_map[(a, h)] = e
    es_keys_remaining = set(es_map.keys())

    # Do we have a 'winner' column? (Postgres information_schema)
    try:
        has_winner_col = (
            db.session.execute(
                _text(
                    """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'games' AND column_name = 'winner'
                LIMIT 1
            """
                )
            ).scalar()
            is not None
        )
    except Exception:
        has_winner_col = False

    # Pull DB games for the target week
    if has_winner_col:
        rows = (
            db.session.execute(
                _text(
                    """
                SELECT g.id, g.away_team, g.home_team,
                       g.status, g.home_score, g.away_score, g.winner
                FROM games g
                JOIN weeks wk ON wk.id = g.week_id
                WHERE wk.season_year = :y AND wk.week_number = :w
                ORDER BY g.game_time
            """
                ),
                {"y": season_year, "w": week},
            )
            .mappings()
            .all()
        )
    else:
        rows = (
            db.session.execute(
                _text(
                    """
                SELECT g.id, g.away_team, g.home_team,
                       g.status, g.home_score, g.away_score
                FROM games g
                JOIN weeks wk ON wk.id = g.week_id
                WHERE wk.season_year = :y AND wk.week_number = :w
                ORDER BY g.game_time
            """
                ),
                {"y": season_year, "w": week},
            )
            .mappings()
            .all()
        )

    linkable = 0  # how many DB games had a corresponding ESPN event
    changed = 0  # how many DB rows we actually modified this run
    updated_scores = 0
    updated_status = 0
    updated_winner = 0

    missing_in_espn = []  # DB games we couldn't find on ESPN
    matched_keys = set()

    # Map ESPN state -> our DB status
    state_to_status = {"pre": "scheduled", "in": "in_progress", "post": "final"}

    for r in rows:
        db_id = r["id"]
        db_away = (r["away_team"] or "").strip()
        db_home = (r["home_team"] or "").strip()
        key = (db_away.lower(), db_home.lower())

        ev = es_map.get(key)
        if not ev:
            missing_in_espn.append(f"{db_away} @ {db_home}")
            continue

        # Found a linkable pair
        linkable += 1
        matched_keys.add(key)
        if key in es_keys_remaining:
            es_keys_remaining.remove(key)

        # ESPN values
        es_state = (ev.get("state") or "").lower()
        es_status = state_to_status.get(es_state)  # None if unknown
        es_home_score = ev.get("home_score")
        es_away_score = ev.get("away_score")

        # Current DB values
        cur_status = r["status"]
        cur_home_score = r["home_score"]
        cur_away_score = r["away_score"]
        cur_winner = r.get("winner") if has_winner_col else None

        # Determine winner from ESPN scores (if present)
        new_winner = None
        if (
            isinstance(es_home_score, int)
            and isinstance(es_away_score, int)
            and es_home_score != es_away_score
        ):
            new_winner = db_home if es_home_score > es_away_score else db_away

        # Build updates
        sets = []
        params = {"id": db_id}

        # Status
        if es_status and es_status != cur_status:
            sets.append("status = :status")
            params["status"] = es_status
            updated_status += 1

        # Scores
        if es_home_score is not None and es_home_score != cur_home_score:
            sets.append("home_score = :home_score")
            params["home_score"] = es_home_score
        if es_away_score is not None and es_away_score != cur_away_score:
            sets.append("away_score = :away_score")
            params["away_score"] = es_away_score
        if ("home_score" in params) or ("away_score" in params):
            updated_scores += 1

        # Winner (only if column exists, decisive score, and value actually changes)
        if has_winner_col and new_winner and (cur_winner != new_winner):
            sets.append("winner = :winner")
            params["winner"] = new_winner
            updated_winner += 1

        if sets:
            sql = f"UPDATE games SET {', '.join(sets)} WHERE id = :id"
            db.session.execute(_text(sql), params)
            changed += 1

    # Commit once at the end for performance
    if changed:
        db.session.commit()

    # ESPN events that didn't find a DB counterpart
    unmatched_espn = []
    for a, h in es_keys_remaining:
        # Pretty format using original-cased names if available
        e = es_map[(a, h)]
        unmatched_espn.append(f"{e.get('away_team','?')} @ {e.get('home_team','?')}")

    return {
        "season_year": season_year,
        "week": week,
        "total_games": len(rows),
        "linkable": linkable,  # NEW: how many DB games were matchable by names
        "matched": changed,  # kept semantics: rows actually changed this run
        "updated_scores": updated_scores,
        "updated_winner": updated_winner if has_winner_col else 0,
        "updated_status": updated_status,
        "missing_in_espn": missing_in_espn,
        "unmatched_espn": unmatched_espn,
    }


def cron_syncscores() -> dict:
    """
    Pick the latest week that is actually active (in-progress or completed)
    using BOTH DB signals and ESPN state, then sync from ESPN.
    """
    from sqlalchemy import text as _text

    app = create_app()
    with app.app_context():
        # 1) ESPN current context
        espn_year = espn_type = espn_week = None
        try:
            espn_year, espn_type, espn_week = detect_current_context()
            logger.info(f"ESPN context -> year={espn_year} type={espn_type} week={espn_week}")
        except Exception:
            logger.exception("detect_current_context failed")

        # 2) Season selection (prefer ESPN's if present in DB)
        season = None
        if espn_year is not None:
            has_espn_season = db.session.execute(
                _text("SELECT 1 FROM weeks WHERE season_year=:y LIMIT 1"),
                {"y": espn_year},
            ).scalar()
            if has_espn_season is not None:
                season = espn_year
        if season is None:
            season = db.session.execute(_text("SELECT MAX(season_year) FROM weeks")).scalar()
        if not season:
            logger.warning("cron_syncscores: no season_year in weeks")
            return {"error": "no season_year in weeks"}

        # 3) Weeks list (DESC)
        weeks_rows = db.session.execute(
            _text("SELECT week_number FROM weeks WHERE season_year=:y ORDER BY week_number DESC"),
            {"y": season},
        ).all()
        weeks = [r[0] for r in weeks_rows]
        if not weeks:
            logger.warning("cron_syncscores: no weeks found for season %s", season)
            return {"error": f"no weeks found for season {season}"}

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Prefer ESPN's week if present in DB
        chosen = espn_week if (espn_week is not None and espn_week in weeks) else None

        # 4) Only scan if we don't already have ESPN's week;
        #    and never allow a FUTURE week (> espn_week) to override
        if chosen is None:
            scan_weeks = weeks
            if espn_week is not None:
                scan_weeks = [w for w in weeks if w <= espn_week]
        else:
            scan_weeks = []

        for w in scan_weeks:
            # DB signals
            stats = (
                db.session.execute(
                    _text(
                        """
                    SELECT
                      SUM(
                        CASE
                          WHEN g.status IN ('final','in_progress')
                               OR (g.home_score IS NOT NULL AND g.away_score IS NOT NULL)
                          THEN 1 ELSE 0
                        END
                      ) AS progressed,
                      MIN(g.game_time) AS first_kick
                    FROM games g
                    JOIN weeks wk ON wk.id = g.week_id
                    WHERE wk.season_year=:y AND wk.week_number=:w
                    """
                    ),
                    {"y": season, "w": w},
                )
                .mappings()
                .first()
            )

            db_active = False
            if stats:
                progressed = int(stats["progressed"] or 0)
                first_kick = stats["first_kick"]
                db_active = (progressed > 0) or (first_kick is not None and first_kick <= now)

            # ESPN signals
            espn_active = False
            try:
                evs = fetch_espn_scoreboard(w, season)
                espn_active = any(
                    (e.get("state") in ("in", "post"))
                    or (e.get("home_score") is not None and e.get("away_score") is not None)
                    for e in evs
                )
            except Exception:
                logger.exception("cron_syncscores: ESPN probe failed for week %s", w)

            if db_active or espn_active:
                chosen = w
                break

        # Fallback
        if chosen is None:
            chosen = weeks[0]

        # 5) IMPORTANT: run sync **inside** app context
        summary = sync_week_scores_from_espn(chosen, season)
        logger.info("cron_syncscores summary: %s", summary)
        return summary


def _now_utc_naive():
    """UTC 'now' as naive datetime to match 'timestamp without time zone' columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _pt(dt_utc):
    """Format a UTC datetime in a friendly way (US/Eastern as example)."""
    if not dt_utc:
        return ""
    try:
        eastern = dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("US/Eastern"))
        return eastern.strftime("%a %b %-d @ %-I:%M %p ET")
    except Exception:
        return str(dt_utc)


async def start(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    user = update.effective_user
    chat_id = str(update.effective_chat.id)
    username = (user.username or "").strip()
    full_name = (getattr(user, "full_name", None) or "").strip()
    first_name = (user.first_name or "").strip()
    logger.info(
        f"📩 /start from {username or full_name or first_name or 'unknown'} (chat_id={chat_id})"
    )

    app = create_app()
    with app.app_context():
        # Already linked?
        existing = Participant.query.filter_by(telegram_chat_id=chat_id).first()
        if existing:
            msg = f"👋 You're already registered as {existing.name}."
            await update.message.reply_text(msg)
            return

        # Try to link to existing participant by name candidates
        linked = None
        candidates = [n for n in {username, full_name, first_name} if n]
        for c in candidates:
            p = Participant.query.filter_by(name=c).first()
            if p:
                p.telegram_chat_id = chat_id
                db.session.commit()
                linked = p
                logger.info(f"🔗 Linked participant '{p.name}' to chat_id {chat_id}")
                break

        if not linked:
            # Create new participant record with a unique name based on Telegram profile
            base = full_name or username or first_name or f"user_{chat_id}"
            name = base
            suffix = 1
            while Participant.query.filter_by(name=name).first():
                suffix += 1
                name = f"{base} ({suffix})"
            p = Participant(name=name, telegram_chat_id=chat_id)
            db.session.add(p)
            db.session.commit()
            linked = p
            logger.info(f"🆕 Created participant '{name}' for chat_id {chat_id}")

    await update.message.reply_text(f"✅ Registered as {linked.name}. You're ready to make picks!")


async def handle_pick(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    try:
        _, game_id_str, team = query.data.split(":", 2)
        game_id = int(game_id_str)
    except Exception:
        await query.edit_message_text("⚠️ Invalid selection payload.")
        return

    chat_id = str(update.effective_chat.id)

    app = create_app()
    with app.app_context():
        participant = Participant.query.filter_by(telegram_chat_id=chat_id).first()
        if not participant:
            await query.edit_message_text("⚠️ Not linked yet. Send /start first.")
            return

        pick = Pick.query.filter_by(participant_id=participant.id, game_id=game_id).first()
        if not pick:
            pick = Pick(participant_id=participant.id, game_id=game_id, selected_team=team)
            db.session.add(pick)
        else:
            pick.selected_team = team
        db.session.commit()

    await query.edit_message_text(f"✅ You picked {team}")


async def deletepicks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    chat_id = str(update.effective_chat.id)
    args = context.args

    # Usage guard
    if len(args) < 2:
        return await m.reply_text(
            'Usage: /deletepicks "<participant name>" <week_number>\n'
            'Example: /deletepicks "Kevin" 2'
        )

    # Parse: last arg = week, everything before = name (allow spaces/quotes)
    try:
        week = int(args[-1])
    except ValueError:
        return await m.reply_text('Week must be an integer. Example: /deletepicks "Kevin" 2')

    name = " ".join(args[:-1]).strip().strip('"').strip("'")
    if not name:
        return await m.reply_text('Provide a participant name. Example: /deletepicks "Kevin" 2')

    # Work inside app context
    from sqlalchemy import text as _text

    from flask_app import create_app
    from models import db as _db

    app = create_app()
    with app.app_context():
        # Simple admin check: only allow Tony's Telegram to run this
        is_admin = (
            _db.session.execute(
                _text(
                    """
            SELECT 1
            FROM participants
            WHERE lower(name)='tony' AND telegram_chat_id = :c
        """
                ),
                {"c": chat_id},
            ).scalar()
            is not None
        )
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Find participant by name (case-insensitive)
        pid = _db.session.execute(
            _text("SELECT id FROM participants WHERE lower(name)=lower(:n)"),
            {"n": name},
        ).scalar()
        if not pid:
            return await m.reply_text(f'No participant named "{name}" found.')

        # Resolve season for the requested week (latest season containing that week)
        season = _db.session.execute(
            _text(
                """
            SELECT season_year
            FROM weeks
            WHERE week_number = :w
            ORDER BY season_year DESC
            LIMIT 1
        """
            ),
            {"w": week},
        ).scalar()
        if not season:
            return await m.reply_text(f"Week {week} not found in table weeks.")

        # Count existing picks first (for report)
        existing = _db.session.execute(
            _text(
                """
            SELECT COUNT(*)
            FROM picks p
            JOIN games g ON g.id = p.game_id
            JOIN weeks w ON w.id = g.week_id
            WHERE p.participant_id = :pid
              AND w.week_number   = :w
              AND w.season_year   = :y
        """
            ),
            {"pid": pid, "w": week, "y": season},
        ).scalar()

        # Delete picks and report how many were removed
        res = _db.session.execute(
            _text(
                """
            DELETE FROM picks p
            USING games g, weeks w
            WHERE p.game_id = g.id
              AND g.week_id = w.id
              AND p.participant_id = :pid
              AND w.week_number    = :w
              AND w.season_year    = :y
            RETURNING p.id
        """
            ),
            {"pid": pid, "w": week, "y": season},
        )
        deleted = len(res.fetchall())
        _db.session.commit()

    await m.reply_text(
        f'🧹 Deleted {deleted} pick(s) for "{name}" in Week {week} ({season}). '
        f"Previously existed: {existing}."
    )


async def syncscores_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      /syncscores <week_number> [season_year]
    Pull ESPN for that week/season and write scores/status/winner into DB.
    Idempotent; safe to run repeatedly.
    """
    m = update.effective_message
    chat_id = str(update.effective_chat.id)
    args = context.args or []
    if not args:
        return await m.reply_text("Usage: /syncscores <week_number> [season_year]")

    # Parse week
    try:
        week = int(args[0])
    except ValueError:
        return await m.reply_text("Week must be an integer, e.g. /syncscores 2")

    # Optional season
    season_year = None
    if len(args) >= 2:
        try:
            season_year = int(args[1])
        except ValueError:
            return await m.reply_text("Season year must be an integer, e.g. 2025")

    from flask_app import create_app

    app = create_app()
    with app.app_context():
        # Admin guard: only Tony's chat ID can invoke
        is_admin = (
            db.session.execute(
                _text(
                    """
            SELECT 1 FROM participants WHERE lower(name)='tony' AND telegram_chat_id=:c
        """
                ),
                {"c": chat_id},
            ).scalar()
            is not None
        )
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Resolve season if not passed
        if season_year is None:
            season_year = db.session.execute(
                _text(
                    """
                SELECT season_year FROM weeks
                WHERE week_number=:w
                ORDER BY season_year DESC LIMIT 1
            """
                ),
                {"w": week},
            ).scalar()
            if not season_year:
                return await m.reply_text(f"Week {week} not found in weeks.")

        summary = sync_week_scores_from_espn(week, season_year)

    # Compact summary
    lines = [
        f"🔄 Synced ESPN → DB for Week {summary['week']} ({summary['season_year']})",
        f"Games in DB: {summary['total_games']}  |  No change: {summary['matched']}",
        f"Updated → scores: {summary['updated_scores']}  winner: {summary['updated_winner']}  status: {summary['updated_status']}",
    ]
    if summary["missing_in_espn"]:
        samp = ", ".join(summary["missing_in_espn"][:3])
        lines.append(
            f"Missing in ESPN ({len(summary['missing_in_espn'])}): {samp}{' …' if len(summary['missing_in_espn'])>3 else ''}"
        )
    if summary["unmatched_espn"]:
        samp = ", ".join(summary["unmatched_espn"][:3])
        lines.append(
            f"Extra on ESPN ({len(summary['unmatched_espn'])}): {samp}{' …' if len(summary['unmatched_espn'])>3 else ''}"
        )

    await m.reply_text("\n".join(lines))


async def whoisleft_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    chat_id = str(update.effective_chat.id)

    # Parse args
    if not context.args:
        return await m.reply_text("Usage: /whoisleft <week_number>   (e.g., /whoisleft 2)")
    try:
        week = int(context.args[0])
    except ValueError:
        return await m.reply_text("Week must be an integer, e.g. /whoisleft 2")

    # DB work
    from flask_app import create_app
    from models import db as _db

    app = create_app()
    with app.app_context():
        # Admin guard: only Tony’s Telegram
        is_admin = (
            _db.session.execute(
                _text(
                    """
            SELECT 1 FROM participants WHERE lower(name)='tony' AND telegram_chat_id=:c
        """
                ),
                {"c": chat_id},
            ).scalar()
            is not None
        )
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Resolve season for that week (latest available)
        season = _db.session.execute(
            _text(
                """
            SELECT season_year FROM weeks WHERE week_number=:w ORDER BY season_year DESC LIMIT 1
        """
            ),
            {"w": week},
        ).scalar()
        if not season:
            return await m.reply_text(f"Week {week} not found in table weeks.")

        # Total games in that week
        total_games = _db.session.execute(
            _text(
                """
            SELECT COUNT(*) FROM games g JOIN weeks w ON w.id=g.week_id
            WHERE w.season_year=:y AND w.week_number=:w
        """
            ),
            {"y": season, "w": week},
        ).scalar()

        # Per-user picked count
        rows = (
            _db.session.execute(
                _text(
                    """
            WITH wg AS (
              SELECT g.id
              FROM games g JOIN weeks w ON w.id=g.week_id
              WHERE w.season_year=:y AND w.week_number=:w
            )
            SELECT u.id, u.name, u.telegram_chat_id,
                   COALESCE(COUNT(p.selected_team),0) AS picked
            FROM participants u
            LEFT JOIN picks p
              ON p.participant_id=u.id AND p.game_id IN (SELECT id FROM wg) AND p.selected_team IS NOT NULL
            GROUP BY u.id, u.name, u.telegram_chat_id
            ORDER BY u.id
        """
                ),
                {"y": season, "w": week},
            )
            .mappings()
            .all()
        )

    # Build summary
    lines = [f"Week {week} ({season}) — total games: {total_games}"]
    for r in rows:
        remaining = (total_games or 0) - int(r["picked"] or 0)
        lines.append(
            f"• {r['name']}: picked {int(r['picked'] or 0)}/{total_games} — remaining {remaining}"
        )
    await m.reply_text("\n".join(lines))


# /remindweek <week> [participant name...]
# If no name is supplied, nudges everyone with remaining picks.
# Only sends games with g.game_time > now (future), and where no pick exists.


async def remindweek_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    chat_id = str(update.effective_chat.id)

    if not context.args:
        return await m.reply_text(
            "Usage: /remindweek <week_number> [participant name]\n"
            "Examples:\n"
            "  /remindweek 2\n"
            "  /remindweek 2 Kevin\n"
            '  /remindweek 2 "Wil Eddie Cano"'
        )

    try:
        week = int(context.args[0])
    except ValueError:
        return await m.reply_text("Week must be an integer, e.g. /remindweek 2")

    name = (
        " ".join(context.args[1:]).strip().strip('"').strip("'") if len(context.args) > 1 else None
    )

    from flask_app import create_app
    from models import db as _db

    app = create_app()
    now_cutoff = _now_utc_naive()

    with app.app_context():
        # Admin guard
        is_admin = (
            _db.session.execute(
                _text(
                    """
            SELECT 1 FROM participants WHERE lower(name)='tony' AND telegram_chat_id=:c
        """
                ),
                {"c": chat_id},
            ).scalar()
            is not None
        )
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Resolve season
        season = _db.session.execute(
            _text(
                """
            SELECT season_year FROM weeks WHERE week_number=:w ORDER BY season_year DESC LIMIT 1
        """
            ),
            {"w": week},
        ).scalar()
        if not season:
            return await m.reply_text(f"Week {week} not found in table weeks.")

        # Decide target participants
        if name:
            targets = (
                _db.session.execute(
                    _text(
                        """
                SELECT id, name, telegram_chat_id
                FROM participants
                WHERE lower(name)=lower(:n)
            """
                    ),
                    {"n": name},
                )
                .mappings()
                .all()
            )
            if not targets:
                return await m.reply_text(f'No participant named "{name}" found.')
        else:
            # Everyone with remaining unpicked games
            targets = (
                _db.session.execute(
                    _text(
                        """
              WITH wg AS (
                SELECT g.id
                FROM games g JOIN weeks w ON w.id=g.week_id
                WHERE w.season_year=:y AND w.week_number=:w
              )
              SELECT u.id, u.name, u.telegram_chat_id,
                     ((SELECT COUNT(*) FROM wg)
                      - COALESCE(COUNT(p.selected_team),0)) AS remaining
              FROM participants u
              LEFT JOIN picks p
                ON p.participant_id=u.id AND p.game_id IN (SELECT id FROM wg) AND p.selected_team IS NOT NULL
              GROUP BY u.id, u.name, u.telegram_chat_id
              HAVING ((SELECT COUNT(*) FROM wg) - COALESCE(COUNT(p.selected_team),0)) > 0
              ORDER BY u.id
            """
                    ),
                    {"y": season, "w": week},
                )
                .mappings()
                .all()
            )

        sent_total = 0
        for u in targets:
            if not u["telegram_chat_id"]:
                continue  # cannot DM

            # Unpicked, future games only
            rows = (
                _db.session.execute(
                    _text(
                        """
                SELECT g.id AS game_id, g.away_team, g.home_team, g.game_time,
                       g.favorite_team, g.spreads_pts
                FROM games g
                JOIN weeks w ON w.id=g.week_id
                LEFT JOIN picks p ON p.game_id=g.id AND p.participant_id=:pid
                WHERE w.season_year=:y AND w.week_number=:w
                  AND (p.id IS NULL OR p.selected_team IS NULL)
                  AND (g.game_time IS NULL OR g.game_time > :now)  -- future only
                ORDER BY g.game_time NULLS LAST, g.id
            """
                    ),
                    {"pid": u["id"], "y": season, "w": week, "now": now_cutoff},
                )
                .mappings()
                .all()
            )

            if not rows:
                # Optionally let them know they’re all set / or only past games remain
                _send_message(
                    u["telegram_chat_id"],
                    f"✅ {u['name']}: you’re all set for Week {week} ({season}).",
                )
                continue

            # Send one message per game with two buttons
            for r in rows:
                kb = {
                    "inline_keyboard": [
                        [
                            {
                                "text": r["away_team"],
                                "callback_data": f"pick:{r['game_id']}:{r['away_team']}",
                            }
                        ],
                        [
                            {
                                "text": r["home_team"],
                                "callback_data": f"pick:{r['game_id']}:{r['home_team']}",
                            }
                        ],
                    ]
                }
                _send_message(
                    u["telegram_chat_id"],
                    f"{r['away_team']} @ {r['home_team']}",
                    reply_markup=kb,
                )
                sent_total += 1

    await m.reply_text(f"📨 Reminders sent: {sent_total} game messages.")


async def getscores_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      /getscores <week_number> [all]

    Shows each participant's wins/losses for the specified week based on
    games that have a recorded winner (i.e., completed so far).
    If 'all' is provided, also DM the same scoreboard to every participant.
    """
    m = update.effective_message
    chat_id = str(update.effective_chat.id)
    args = context.args or []

    if not args:
        return await m.reply_text("Usage: /getscores <week_number> [all]")

    # Parse week
    try:
        week = int(args[0])
    except ValueError:
        return await m.reply_text("Week must be an integer, e.g. /getscores 2")

    broadcast = len(args) > 1 and args[1].lower() == "all"

    from flask_app import create_app
    from models import db as _db

    app = create_app()
    with app.app_context():
        # Admin guard (only Tony's Telegram chat may invoke)
        is_admin = (
            _db.session.execute(
                _text(
                    """
            SELECT 1 FROM participants WHERE lower(name)='tony' AND telegram_chat_id=:c
        """
                ),
                {"c": chat_id},
            ).scalar()
            is not None
        )
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Resolve season for this week (latest)
        season = _db.session.execute(
            _text(
                """
            SELECT season_year FROM weeks
            WHERE week_number=:w
            ORDER BY season_year DESC
            LIMIT 1
        """
            ),
            {"w": week},
        ).scalar()
        if not season:
            return await m.reply_text(f"Week {week} not found in table weeks.")

            # Completed games in this week (explicit winner OR both scores set and not a tie)
        total_completed = (
            _db.session.execute(
                _text(
                    """
            SELECT COUNT(*)
            FROM games g
            JOIN weeks w ON w.id = g.week_id
            WHERE w.season_year=:y
              AND w.week_number=:w
              AND (
                    g.winner IS NOT NULL
                 OR (g.home_score IS NOT NULL AND g.away_score IS NOT NULL AND g.home_score <> g.away_score)
              )
        """
                ),
                {"y": season, "w": week},
            ).scalar()
            or 0
        )

        if total_completed == 0:
            return await m.reply_text(f"No games completed yet for Week {week} ({season}).")

        # Per-participant wins/losses for completed games
        rows = (
            _db.session.execute(
                _text(
                    """
            WITH wg AS (
              SELECT
                g.id,
                CASE
                  WHEN g.winner IS NOT NULL THEN g.winner
                  WHEN g.home_score IS NOT NULL AND g.away_score IS NOT NULL AND g.home_score <> g.away_score
                       THEN CASE WHEN g.home_score > g.away_score THEN g.home_team ELSE g.away_team END
                  ELSE NULL
                END AS winner
              FROM games g
              JOIN weeks w ON w.id = g.week_id
              WHERE w.season_year=:y
                AND w.week_number=:w
                AND (
                     g.winner IS NOT NULL
                  OR (g.home_score IS NOT NULL AND g.away_score IS NOT NULL AND g.home_score <> g.away_score)
                )
            )
            SELECT u.id,
                   u.name,
                   u.telegram_chat_id,
                   COALESCE(SUM(CASE WHEN p.selected_team = wg.winner THEN 1 ELSE 0 END), 0) AS wins,
                   COALESCE(SUM(CASE WHEN p.selected_team IS NOT NULL AND p.selected_team <> wg.winner THEN 1 ELSE 0 END), 0) AS losses
            FROM participants u
            LEFT JOIN picks p ON p.participant_id = u.id
            LEFT JOIN wg ON wg.id = p.game_id
            GROUP BY u.id, u.name, u.telegram_chat_id
            ORDER BY wins DESC, u.name
        """
                ),
                {"y": season, "w": week},
            )
            .mappings()
            .all()
        )

        title = f"📈 Scoreboard — Week {week} ({season})  [completed games: {total_completed}]"
        body_lines = [title, ""]
        for r in rows:
            body_lines.append(f"• {r['name']}: {int(r['wins'])}-{int(r['losses'])}")
        body = "\n".join(body_lines)

        # Reply in invoking chat
        await m.reply_text(body)

        # Optional broadcast
        if broadcast:
            sent = 0
            for r in rows:
                if r["telegram_chat_id"]:
                    try:
                        _send_message(r["telegram_chat_id"], body)
                        sent += 1
                    except Exception:
                        logger.exception("Failed sending /getscores to %s", r["name"])
            await m.reply_text(f"✅ Sent scoreboard to {sent} participant(s).")

async def seepicks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      /seepicks <week_number> all
      /seepicks <week_number> <participant_name>

    - If 'all', compiles a grid of everyone's picks for that week and broadcasts
      the grid to each participant (DM) and replies in the invoking chat.
    - If a participant name is provided, shows only that person's picks for the week
      (replies in chat and DM to that participant if linked).
    """
    m = update.effective_message
    chat_id = str(update.effective_chat.id)
    args = context.args or []

    # Validate args
    if len(args) < 2:
        return await m.reply_text(
            "Usage: /seepicks <week_number> <participant|all>\nExample: /seepicks 3 all"
        )

    # Parse week
    try:
        week = int(args[0])
    except ValueError:
        return await m.reply_text("Week must be an integer, e.g. /seepicks 3 all")

    target = " ".join(args[1:]).strip().strip('"').strip("'")  # allow spaces/quotes in names
    is_all = target.lower() == "all"

    from flask_app import create_app
    from models import db as _db

    app = create_app()
    with app.app_context():
        # Admin guard (only Tony's Telegram can run this)
        is_admin = (
            _db.session.execute(
                _text(
                    """
            SELECT 1 FROM participants WHERE lower(name)='tony' AND telegram_chat_id=:c
        """
                ),
                {"c": chat_id},
            ).scalar()
            is not None
        )
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Latest season that has this week
        season = _db.session.execute(
            _text(
                """
            SELECT season_year FROM weeks WHERE week_number=:w ORDER BY season_year DESC LIMIT 1
        """
            ),
            {"w": week},
        ).scalar()
        if not season:
            return await m.reply_text(f"Week {week} not found in table weeks.")

        # Games in the week
        games = (
            _db.session.execute(
                _text(
                    """
            SELECT g.id, g.away_team, g.home_team
            FROM games g
            JOIN weeks w ON w.id = g.week_id
            WHERE w.season_year=:y AND w.week_number=:w
            ORDER BY g.game_time NULLS LAST, g.id
        """
                ),
                {"y": season, "w": week},
            )
            .mappings()
            .all()
        )
        if not games:
            return await m.reply_text(f"No games found for Week {week} ({season}).")

        # Participants scope
        if is_all:
            participants = (
                _db.session.execute(
                    _text(
                        """
                SELECT id, name, telegram_chat_id
                FROM participants
                ORDER BY name
            """
                    )
                )
                .mappings()
                .all()
            )
        else:
            row = (
                _db.session.execute(
                    _text(
                        """
                SELECT id, name, telegram_chat_id
                FROM participants
                WHERE lower(name)=lower(:name)
                LIMIT 1
            """
                    ),
                    {"name": target},
                )
                .mappings()
                .first()
            )
            if not row:
                return await m.reply_text(f'Participant "{target}" not found.')
            participants = [row]

        if not participants:
            return await m.reply_text("No participants found.")

        # Picks map (participant_id, game_id) -> selected_team
        picks = (
            _db.session.execute(
                _text(
                    """
            SELECT p.participant_id, p.game_id, p.selected_team
            FROM picks p
            WHERE p.game_id IN (
                SELECT g.id
                FROM games g JOIN weeks w ON w.id=g.week_id
                WHERE w.season_year=:y AND w.week_number=:w
            )
        """
                ),
                {"y": season, "w": week},
            )
            .mappings()
            .all()
        )

        pick_map = {}
        for r in picks:
            if r["selected_team"]:
                pick_map[(r["participant_id"], r["game_id"])] = r["selected_team"]

        # Build output
        header = f"📊 Picks — Week {week} ({season})"
        lines_out = [header, ""]
        for g in games:
            parts = []
            for p in participants:
                team = pick_map.get((p["id"], g["id"]), "—")
                parts.append(f"{p['name']}: {team}")
            lines_out.append(f"{g['away_team']} @ {g['home_team']} — " + ", ".join(parts))
        body = "\n".join(lines_out)

        # Always reply in invoking chat
        await m.reply_text(body)

        # Broadcast in 'all' mode: DM each participant with the full grid
        if is_all:
            sent = 0
            for p in participants:
                if p["telegram_chat_id"]:
                    try:
                        _send_message(p["telegram_chat_id"], body)
                        sent += 1
                    except Exception:
                        logger.exception("Failed sending /seepicks to %s", p["name"])
            await m.reply_text(f"✅ Sent to {sent} participant(s).")
        else:
            # Name mode: DM that person too if linked
            p = participants[0]
            if p["telegram_chat_id"]:
                try:
                    _send_message(p["telegram_chat_id"], body)
                except Exception:
                    logger.exception("Failed sending /seepicks to %s", p["name"])


def run_telegram_listener():
    """Run polling listener so /start and button taps are processed."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    from telegram import Update
    from telegram.ext import (  # local import to avoid import-time failures
        Application,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
    )

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_pick))
    application.add_handler(CommandHandler("sendweek", sendweek_command))
    application.add_handler(CommandHandler("syncscores", syncscores_command))
    application.add_handler(CommandHandler("getscores", getscores_command))
    application.add_handler(CommandHandler("deletepicks", deletepicks_command))
    application.add_handler(CommandHandler("whoisleft", whoisleft_command))
    application.add_handler(CommandHandler("seepicks", seepicks_command))
    application.add_handler(CommandHandler("remindweek", remindweek_command))
    application.run_polling()


def _send_message(chat_id: str, text: str, reply_markup=None):
    # DEBUG: log exactly what we're sending (single line)
    try:
        print("DEBUG_SEND:", text.replace("\n", " | "))
    except Exception:
        pass

    import os, httpx
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    with httpx.Client(timeout=20) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()


def _spread_label(game) -> str:
    """
    Pretty label for point spread.
    Accepts ORM object or mapping/dict with keys: favorite_team, spread_pts
    Examples: 'fav: Jaguars -3.5', 'TBD'
    """
    # pull fields from ORM or dict
    if isinstance(game, dict):
        fav = game.get("favorite_team")
        spr = game.get("spread_pts")
    else:
        fav = getattr(game, "favorite_team", None)
        spr = getattr(game, "spread_pts", None)

    if not fav or spr is None:
        return "TBD"

    # coerce to float (Decimal/str safe)
    try:
        s = float(spr)
    except Exception:
        return "TBD"

    # always display as favorite giving points (negative)
    if s == 0:
        shown = "PK"              # or use "±0" if you prefer
    else:
        shown = f"-{abs(s):g}"

    return f"fav: {fav} {shown}"


def _pt(dt_like, tzname: str = "America/Los_Angeles") -> str:
    """
    Pretty-print a kickoff time in Pacific time.
    Accepts a datetime (naive UTC or tz-aware) or an ISO-like string.
    Returns e.g. "Thu 10/16 05:15 PM PT".
    """
    if not dt_like:
        return "TBD"

    # Normalize to datetime
    d = None
    if isinstance(dt_like, str):
        s = dt_like.strip().replace("Z", "+00:00")
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                d = _dt.datetime.strptime(s, fmt)
                break
            except Exception:
                continue
        if d is None:
            try:
                d = _dt.datetime.fromisoformat(s)
            except Exception:
                return str(dt_like)
    else:
        d = dt_like

    # Assume DB datetimes are UTC if naive
    if d.tzinfo is None:
        d = d.replace(tzinfo=ZoneInfo("UTC"))

    local = d.astimezone(ZoneInfo(tzname))
    # Use PT as a stable label (DST becomes PDT/PST automatically, label stays PT)
    return local.strftime("%a %m/%d %I:%M %p PT")


def send_week_games(week_number: int, season_year: int):
    """
    Send ALL games for the week to every participant who has a telegram_chat_id.
    Includes kickoff time in PT and the spread line if present.
    """
    app = create_app()
    with app.app_context():
        week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()
        if not week:
            logger.error(f"❌ No week found for {season_year} W{week_number}")
            return

        games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time, Game.id).all()
        if not games:
            logger.error(f"❌ No games found for {season_year} W{week_number}")
            return

        participants = Participant.query.filter(Participant.telegram_chat_id.isnot(None)).all()
        for part in participants:
            chat_id = str(part.telegram_chat_id)
            for g in games:
                kb = {
                    "inline_keyboard": [
                        [{"text": g.away_team, "callback_data": f"pick:{g.id}:{g.away_team}"}],
                        [{"text": g.home_team, "callback_data": f"pick:{g.id}:{g.home_team}"}],
                    ]
                }
                msg = f"{g.away_team} @ {g.home_team}\n{_pt(g.game_time)}\n{_spread_label(g)}"
                try:
                    _send_message(chat_id, msg, reply_markup=kb)
                    logger.info(f"✅ Sent game to {part.name}: {g.away_team} @ {g.home_team}")
                except Exception as e:
                    logger.exception("❌ Failed to send game message: %s", e)

async def sendweek_command(update, context):
    """
    Usage:
      /sendweek <week>            -> send to ALL (broadcast)
      /sendweek <week> dry        -> DRY-RUN (counts only)
      /sendweek <week> me         -> send ONLY to the caller (admin)
      /sendweek <week> <name...>  -> send ONLY to that participant by name
    """
    import asyncio
    user = update.effective_user
    if ADMIN_IDS and (not user or user.id not in ADMIN_IDS):
        if update.message:
            await update.message.reply_text("Sorry, admin only.")
        return

    args = context.args or []
    if not args or not args[0].isdigit():
        if update.message:
            await update.message.reply_text(
                "Usage: /sendweek <week_number> [dry|me|<participant name>]"
            )
        return

    week_number = int(args[0])
    target = "all" if len(args) == 1 else " ".join(args[1:]).strip()

    def _find_existing_week_info():
        row = (
            db.session.execute(
                T("""
                   SELECT w.id AS week_id, w.season_year
                     FROM weeks w
                    WHERE w.week_number = :w
                 ORDER BY w.season_year DESC
                    LIMIT 1
                """),
                {"w": week_number},
            ).mappings().first()
        )
        return dict(row) if row else None

    def _send_to_one(participant_id: int, chat_id: str, season_year: int) -> int:
        rows = (
            db.session.execute(
                T("""
                SELECT g.id, g.away_team, g.home_team, g.game_time,
                       g.favorite_team, g.spread_pts
                  FROM games g
                  JOIN weeks w ON w.id = g.week_id
             LEFT JOIN picks p
                    ON p.game_id = g.id
                   AND p.participant_id = :pid
                 WHERE w.season_year = :y
                   AND w.week_number = :w
                   AND (p.id IS NULL OR p.selected_team IS NULL)
              ORDER BY g.game_time NULLS LAST, g.id
                """),
                {"pid": participant_id, "y": season_year, "w": week_number},
            ).mappings().all()
        )

        sent = 0
        for g in rows:
            kb = {
                "inline_keyboard": [
                    [{"text": g["away_team"], "callback_data": f"pick:{g['id']}:{g['away_team']}"}],
                    [{"text": g["home_team"], "callback_data": f"pick:{g['id']}:{g['home_team']}"}],
                ]
            }
            text = (
                f"{g['away_team']} @ {g['home_team']}\n"
                f"{_pt(g['game_time'])}\n"
                f"{_spread_label(g)}"
            )
            _send_message(str(chat_id), text, reply_markup=kb)
            sent += 1
        return sent

    # targeted (dry/me/name)
    if target.lower() in ("dry", "me") or target.lower() not in ("all",):
        app = create_app()
        with app.app_context():
            wkinfo = _find_existing_week_info()
            if not wkinfo:
                if update.message:
                    await update.message.reply_text(
                        f"Week {week_number} not found yet. (Dry/me/name modes do not auto-create.)"
                    )
                return
            season_year = int(wkinfo["season_year"])

            if target.lower() == "dry":
                people = (
                    db.session.execute(
                        T("SELECT id, name, telegram_chat_id FROM participants WHERE telegram_chat_id IS NOT NULL")
                    ).mappings().all()
                )
                total_msgs = 0
                for u in people:
                    cnt = db.session.execute(
                        T("""
                           SELECT COUNT(*)
                             FROM games g
                             JOIN weeks w ON w.id = g.week_id
                        LEFT JOIN picks p
                               ON p.game_id = g.id
                              AND p.participant_id = :pid
                            WHERE w.season_year = :y
                              AND w.week_number = :w
                              AND (p.id IS NULL OR p.selected_team IS NULL)
                        """),
                        {"pid": u["id"], "y": season_year, "w": week_number},
                    ).scalar()
                    total_msgs += int(cnt or 0)
                await update.message.reply_text(
                    f"DRY RUN: would send {total_msgs} button message(s) to {len(people)} participant(s) "
                    f"for Week {week_number} ({season_year})."
                )
                return

            if target.lower() == "me":
                me_chat = str(update.effective_chat.id)
                person = (
                    db.session.execute(
                        T("SELECT id, telegram_chat_id FROM participants WHERE telegram_chat_id = :c"),
                        {"c": me_chat},
                    ).mappings().first()
                )
                if not person:
                    await update.message.reply_text("You're not linked yet. Send /start first.")
                    return
                sent = _send_to_one(person["id"], person["telegram_chat_id"], season_year)
                await update.message.reply_text(f"✅ Sent {sent} unpicked game(s) for Week {week_number} to you.")
                return

            # by name
            name = target
            person = (
                db.session.execute(
                    T("SELECT id, name, telegram_chat_id FROM participants WHERE LOWER(name)=LOWER(:n)"),
                    {"n": name},
                ).mappings().first()
            )
            if not person:
                await update.message.reply_text(f"Participant '{name}' not found.")
                return
            if not person["telegram_chat_id"]:
                await update.message.reply_text(
                    f"Participant '{name}' has no Telegram chat linked. Ask them to /start."
                )
                return
            sent = _send_to_one(person["id"], person["telegram_chat_id"], season_year)
            await update.message.reply_text(f"✅ Sent {sent} unpicked game(s) for Week {week_number} to {person['name']}.")
            return

    # broadcast to all
    async def _do_broadcast():
        app = create_app()
        with app.app_context():
            yr = db.session.execute(
                T("""
                   SELECT season_year FROM weeks
                    WHERE week_number=:w
                 ORDER BY season_year DESC
                    LIMIT 1
                """),
                {"w": week_number},
            ).scalar()
            if yr is None:
                return
            send_week_games(week_number=week_number, season_year=int(yr))

    if update.message:
        await update.message.reply_text(f"Sending Week {week_number} to all registered participants…")
    await asyncio.to_thread(_do_broadcast)
    if update.message:
        await update.message.reply_text("✅ Done.")


if __name__ == "__main__":
    import json
    import sys

    cmd = sys.argv[1] if len(sys.argv) >= 2 else None

    if cmd == "cron":
        # Sync the CURRENT week (auto-detected) from ESPN to DB
        print(json.dumps(cron_syncscores()))

    elif cmd == "sendweek_upcoming":
        # Auto-detect the next week (first kickoff > now) and broadcast picks
        print(json.dumps(cron_send_upcoming_week()))

    elif cmd == "sendweek":
        # Manual broadcast: python jobs.py sendweek <week> [season_year]
        if len(sys.argv) < 3:
            raise SystemExit("Usage: python jobs.py sendweek <week> [season_year]")
        week = int(sys.argv[2])

        app = create_app()
        with app.app_context():
            if len(sys.argv) >= 4:
                season_year = int(sys.argv[3])
            else:
                from sqlalchemy import text as _text

                from models import db

                season_year = db.session.execute(
                    _text(
                        """
                        SELECT season_year
                        FROM weeks
                        WHERE week_number = :w
                        ORDER BY season_year DESC
                        LIMIT 1
                    """
                    ),
                    {"w": week},
                ).scalar()
                if not season_year:
                    raise SystemExit(f"Week {week} not found in any season.")

            # Send the week to all participants with telegram_chat_id
            send_week_games(week, season_year)

        print(json.dumps({"season_year": season_year, "week": week, "status": "sent"}))

    elif cmd == "import-week":
        # Import a specific week's games from ESPN into the DB:
        #   python jobs.py import-week <season_year> <week>
        if len(sys.argv) < 4:
            raise SystemExit("Usage: python jobs.py import-week <season_year> <week>")
        season_year = int(sys.argv[2])
        week = int(sys.argv[3])
        print(json.dumps(import_week_from_espn(season_year, week)))

    elif cmd == "import-week-upcoming":
        # Import the upcoming week (Tue-guarded) so the 9am sender has data:
        #   python jobs.py import-week-upcoming
        print(json.dumps(cron_import_upcoming_week()))

    elif cmd == "import-odds-upcoming":
        # Refresh spreads for the upcoming week from The Odds API:
        #   python jobs.py import-odds-upcoming
        import_odds_upcoming()   # <-- call the function you added above
        print(json.dumps({"status": "odds_imported"}))

    elif cmd == "announce-winners":
        # Tuesday-guarded: announce last week's winners + season totals
        print(json.dumps(cron_announce_weekly_winners()))

    elif cmd == "announce-winners-now":
        # FORCE an immediate announcement (bypasses Tuesday guard) — will SEND
        import os
        from datetime import datetime, timezone

        import httpx
        from sqlalchemy import text as _text

        from flask_app import create_app
        from models import db

        app = create_app()
        with app.app_context():
            season = _get_latest_season_year()
            if not season:
                raise SystemExit("No season_year found.")

            now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            upcoming = _find_upcoming_week_row(season, now_utc_naive)
            if upcoming:
                week_to_announce = max(2, int(upcoming["week_number"]) - 1)
            else:
                last_week = (
                    db.session.execute(
                        _text(
                            "SELECT COALESCE(MAX(week_number),0) FROM weeks WHERE season_year=:y"
                        ),
                        {"y": season},
                    ).scalar()
                    or 0
                )
                week_to_announce = max(2, last_week)

            # Ensure dedupe table, then try to claim this week
            db.session.execute(
                _text(
                    """
                CREATE TABLE IF NOT EXISTS week_announcements (
                    season_year INTEGER NOT NULL,
                    week_number INTEGER NOT NULL,
                    sent_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (season_year, week_number)
                )
            """
                )
            )
            claimed = db.session.execute(
                _text(
                    """
                INSERT INTO week_announcements (season_year, week_number)
                VALUES (:y, :w)
                ON CONFLICT (season_year, week_number) DO NOTHING
                RETURNING 1
            """
                ),
                {"y": season, "w": week_to_announce},
            ).first()
            if not claimed:
                db.session.commit()
                print(
                    json.dumps(
                        {
                            "status": "skipped_duplicate",
                            "season_year": season,
                            "week": week_to_announce,
                        }
                    )
                )
                raise SystemExit(0)

            weekly = _compute_week_results(season, week_to_announce)
            season_totals = _compute_season_totals(season, week_to_announce)
            msg = _format_winners_and_totals(week_to_announce, weekly, season_totals)

            token = os.getenv("TELEGRAM_BOT_TOKEN")
            if not token:
                raise SystemExit("TELEGRAM_BOT_TOKEN not set.")
            url = f"https://api.telegram.org/bot{token}/sendMessage"

            participants = (
                db.session.execute(
                    _text(
                        """
                SELECT id, COALESCE(display_name, name, CONCAT('P', id::text)) AS name, telegram_chat_id
                FROM participants
                WHERE telegram_chat_id IS NOT NULL
            """
                    )
                )
                .mappings()
                .all()
            )

            sent = 0
            with httpx.Client(timeout=20) as client:
                for p in participants:
                    r = client.post(url, json={"chat_id": p["telegram_chat_id"], "text": msg})
                    r.raise_for_status()
                    sent += 1

            db.session.commit()
            print(
                json.dumps(
                    {
                        "status": "sent",
                        "season_year": season,
                        "week": week_to_announce,
                        "recipients": sent,
                        "weekly_top": (weekly[0]["wins"] if weekly else 0),
                        "participants_ranked": len(season_totals),
                    }
                )
            )

    else:
        raise SystemExit(
            "Usage:\n"
            "  python jobs.py cron\n"
            "  python jobs.py sendweek_upcoming\n"
            "  python jobs.py sendweek <week> [season_year]\n"
            "  python jobs.py import-week <season_year> <week>\n"
            "  python jobs.py import-week-upcoming\n"
            "  python jobs.py import-odds-upcoming\n"
            "  python jobs.py announce-winners\n"
            "  python jobs.py announce-winners-now\n"
        )

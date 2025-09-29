import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text as _text

from flask_app import create_app
from models import db

from .admin_alerts import notify_admins
from .config import load_config
from .espn_client import fetch_week
from .time_utils import is_tuesday_local, now_utc, to_naive_utc

log = logging.getLogger("cron_jobs")


def _get_latest_season_year() -> Optional[int]:
    row = db.session.execute(_text("SELECT MAX(season_year) FROM weeks")).scalar()
    return int(row) if row is not None else None


def _find_upcoming_week_row(season: int, now_naive_utc: datetime) -> Optional[dict]:
    return (
        db.session.execute(
            _text(
                """
        SELECT w.season_year, w.week_number,
               MIN(g.game_time) AS first_kick
        FROM weeks w
        LEFT JOIN games g ON g.week_id = w.id
        WHERE w.season_year=:s
        GROUP BY w.season_year, w.week_number
        HAVING MIN(g.game_time) > :now
        ORDER BY MIN(g.game_time)
        LIMIT 1
    """
            ),
            {"s": season, "now": now_naive_utc},
        )
        .mappings()
        .first()
    )


def cron_import_upcoming_week() -> Dict[str, Any]:
    cfg = load_config()
    app = create_app()
    with app.app_context():
        allow_anyday = (os.getenv("ALLOW_ANYDAY") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "y",
        )
        utc_now = now_utc()
        if not allow_anyday and not is_tuesday_local(utc_now, cfg.app_tz):
            log.info("cron_import_upcoming_week: not Tuesday in %s", cfg.app_tz)
            return {"status": "skipped_not_tuesday"}

        season = _get_latest_season_year()
        if not season:
            return {"error": "No season_year in weeks"}

        now_naive = to_naive_utc(utc_now)
        upcoming = _find_upcoming_week_row(season, now_naive)
        if not upcoming:
            return {"status": "no_upcoming_week", "season_year": season}

        week = int(upcoming["week_number"])

        import asyncio

        events = asyncio.run(
            fetch_week(week, season, retries=cfg.espn_retries, backoff_s=cfg.espn_backoff_s)
        )

        if not events:
            asyncio.run(
                notify_admins(
                    cfg.telegram_bot_token,
                    cfg.admin_chat_ids,
                    f"⚠️ ESPN returned 0 events for {season} W{week} during import.",
                )
            )

        # Ensure week row exists
        row = db.session.execute(
            _text(
                """
            INSERT INTO weeks (season_year, week_number)
            VALUES (:y, :w)
            ON CONFLICT (season_year, week_number) DO NOTHING
            RETURNING id
        """
            ),
            {"y": season, "w": week},
        ).first()
        if row:
            week_id = row[0]
        else:
            week_id = db.session.execute(
                _text("SELECT id FROM weeks WHERE season_year=:y AND week_number=:w"),
                {"y": season, "w": week},
            ).scalar()

        state_to_status = {"pre": "scheduled", "in": "in_progress", "post": "final"}
        created = updated = 0

        for ev in events:
            start_dt = to_naive_utc(ev["start_utc"]) if ev.get("start_utc") else None
            status = state_to_status.get(ev.get("state") or "", "scheduled")
            home = ev["home_team"]
            away = ev["away_team"]

            res = db.session.execute(
                _text(
                    """
                UPDATE games
                SET game_time=:game_time, status=:status, home_score=:home_score, away_score=:away_score
                WHERE week_id=:week_id AND lower(home_team)=lower(:home) AND lower(away_team)=lower(:away)
            """
                ),
                {
                    "game_time": start_dt,
                    "status": status,
                    "home_score": ev.get("home_score"),
                    "away_score": ev.get("away_score"),
                    "week_id": week_id,
                    "home": home,
                    "away": away,
                },
            )
            if res.rowcount == 0:
                db.session.execute(
                    _text(
                        """
                    INSERT INTO games (week_id, home_team, away_team, game_time, status, home_score, away_score)
                    VALUES (:week_id, :home, :away, :game_time, :status, :home_score, :away_score)
                """
                    ),
                    {
                        "week_id": week_id,
                        "home": home,
                        "away": away,
                        "game_time": start_dt,
                        "status": status,
                        "home_score": ev.get("home_score"),
                        "away_score": ev.get("away_score"),
                    },
                )
                created += 1
            else:
                updated += 1

        db.session.commit()
        log.info("Imported %s:W%s — created=%s updated=%s", season, week, created, updated)

        count_after = db.session.execute(
            _text(
                """
            SELECT COUNT(*) FROM games g JOIN weeks w ON w.id=g.week_id
            WHERE w.season_year=:y AND w.week_number=:w
        """
            ),
            {"y": season, "w": week},
        ).scalar()

        return {
            "status": "imported",
            "season_year": season,
            "week": week,
            "games": int(count_after or 0),
            "created": created,
            "updated": updated,
        }


def cron_syncscores_latest_active() -> Dict[str, Any]:
    cfg = load_config()
    app = create_app()
    with app.app_context():
        season = _get_latest_season_year()
        if not season:
            return {"error": "No season_year"}

        # Prefer the max week that has at least one game not 'scheduled'
        row = db.session.execute(
            _text(
                """
            SELECT w.week_number
            FROM weeks w
            JOIN games g ON g.week_id=w.id
            WHERE w.season_year=:y AND g.status IN ('in_progress','final')
            GROUP BY w.week_number
            ORDER BY w.week_number DESC
            LIMIT 1
        """
            ),
            {"y": season},
        ).first()

        if row:
            target_week = int(row[0])
        else:
            # fallback to max week that exists
            target_week = int(
                db.session.execute(
                    _text(
                        """
                SELECT COALESCE(MAX(week_number),1) FROM weeks WHERE season_year=:y
            """
                    ),
                    {"y": season},
                ).scalar()
                or 1
            )

        import asyncio

        events = asyncio.run(
            fetch_week(
                target_week,
                season,
                retries=cfg.espn_retries,
                backoff_s=cfg.espn_backoff_s,
            )
        )

        es_map = {(e["away_team"].lower(), e["home_team"].lower()): e for e in events}
        changed = updated_status = updated_scores = 0

        rows = (
            db.session.execute(
                _text(
                    """
            SELECT g.id, g.home_team, g.away_team, g.status, g.home_score, g.away_score
            FROM games g JOIN weeks w ON w.id=g.week_id
            WHERE w.season_year=:y AND w.week_number=:w
        """
                ),
                {"y": season, "w": target_week},
            )
            .mappings()
            .all()
        )

        for r in rows:
            key = (r["away_team"].lower(), r["home_team"].lower())
            ev = es_map.get(key)
            if not ev:
                continue

            new_status = {"pre": "scheduled", "in": "in_progress", "post": "final"}.get(
                ev.get("state") or "", "scheduled"
            )
            new_home = ev.get("home_score")
            new_away = ev.get("away_score")

            if new_status and new_status != r["status"]:
                db.session.execute(
                    _text("UPDATE games SET status=:s WHERE id=:id"),
                    {"s": new_status, "id": r["id"]},
                )
                updated_status += 1
                changed += 1

            if (new_home is not None and new_home != r["home_score"]) or (
                new_away is not None and new_away != r["away_score"]
            ):
                db.session.execute(
                    _text("UPDATE games SET home_score=:hs, away_score=:as WHERE id=:id"),
                    {"hs": new_home, "as": new_away, "id": r["id"]},
                )
                updated_scores += 1
                changed += 1

        db.session.commit()

        return {
            "status": "synced",
            "season_year": season,
            "week": target_week,
            "total_games": len(rows),
            "matched": changed,
            "updated_scores": updated_scores,
            "updated_status": updated_status,
        }

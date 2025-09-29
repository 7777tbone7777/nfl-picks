import os
import logging
from zoneinfo import ZoneInfo
import httpx
import os, asyncio, logging
import json
from telegram.ext import CommandHandler
from sqlalchemy import text as _text
from flask_app import create_app
from models import db, Participant, Week, Game, Pick
from telegram import Update
from telegram.ext import ContextTypes
from datetime import datetime, timezone

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jobs")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_USER_IDS","").replace(" ","").split(",") if x.isdigit()}

# --- ESPN NFL scoreboard (read-only fetch) ---
# Regular season = seasontype=2. Preseason(1), Postseason(3).
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

def fetch_espn_scoreboard(week: int, season_year: int):
    """
    Returns a list of dicts like:
      {
        "away_team": "Washington Commanders",
        "home_team": "Green Bay Packers",
        "away_score": 18 (or None),
        "home_score": 27 (or None),
        "state": "post" | "in" | "pre",
        "winner": "Green Bay Packers" | None
      }
    Does not touch the database.
    """
    import httpx
    with httpx.Client(timeout=15.0, headers={"User-Agent": "nfl-picks-bot/1.0"}) as client:
    # Try both parameter styles ESPN uses. First that succeeds wins.
        urls = [
        f"{ESPN_SCOREBOARD_URL}?week={week}&year={season_year}&seasontype=2",
        f"{ESPN_SCOREBOARD_URL}?week={week}&dates={season_year}&seasontype=2",
        ]
        resp = None
        for u in urls:
            r = client.get(u)
            if r.status_code < 400:
                resp = r
                break
        if resp is None:
        # propagate the last error for visibility
            r.raise_for_status()
        data = resp.json()
        try:
            logger.info("ESPN scoreboard URL used: %s", str(resp.request.url))
        except Exception:
            pass

    out = []
    for ev in data.get("events", []):
        try:
            comp = ev["competitions"][0]
            status = comp.get("status", {}).get("type", {})
            state = (status.get("state") or "").lower()  # "pre" | "in" | "post"

            home = next(t for t in comp["competitors"] if t.get("homeAway") == "home")
            away = next(t for t in comp["competitors"] if t.get("homeAway") == "away")

            home_name = home["team"]["displayName"]
            away_name = away["team"]["displayName"]

            # Scores may be "", None, or a string number.
            def _to_int(x):
                try:
                    return int(x)
                except Exception:
                    return None

            hs = _to_int(home.get("score"))
            as_ = _to_int(away.get("score"))

            # Winner from ESPN if flagged, else derive from scores if final/non-tie
            winner = None
            if home.get("winner") is True:
                winner = home_name
            elif away.get("winner") is True:
                winner = away_name
            elif hs is not None and as_ is not None and hs != as_ and state == "post":
                winner = home_name if hs > as_ else away_name

            out.append({
                "away_team": away_name,
                "home_team": home_name,
                "away_score": as_,
                "home_score": hs,
                "state": state,      # "pre", "in", "post"
                "winner": winner,    # may be None if not final yet or tie
            })
        except Exception:
            # Be resilient to any weird ESPN edge cases
            continue
    return out

def sync_week_scores_from_espn(week: int, season_year: int) -> dict:
    """
    Pull ESPN week data (from Step 1's fetch_espn_scoreboard) and UPDATE games in our DB.
    Idempotent:
      - Only writes fields that actually changed.
      - Never regresses status (won't move final -> in_progress).
      - Never overwrites existing scores/winner with NULL.
    Returns a summary dict with counts.
    """
    from sqlalchemy import text as _text
    from flask_app import create_app

    def _rank_status(s: str | None) -> int:
        s = (s or "").lower()
        if s in ("final", "post"):
            return 3
        if s in ("in", "in_progress", "live"):
            return 2
        return 1  # pre/scheduled/unknown

    app = create_app()
    out = {
        "season_year": season_year,
        "week": week,
        "total_games": 0,
        "matched": 0,
        "updated_scores": 0,
        "updated_winner": 0,
        "updated_status": 0,
        "missing_in_espn": [],
        "unmatched_espn": [],
    }

    with app.app_context():
        # --- ESPN events (requires Step 1 helper) ---
        events = fetch_espn_scoreboard(week, season_year)
        es_map = {(e["away_team"], e["home_team"]): e for e in events}
        seen_keys = set()

        # --- DB games for this week ---
        rows = db.session.execute(_text("""
            SELECT g.id, g.away_team, g.home_team,
                   g.away_score, g.home_score, g.status, g.winner
            FROM games g
            JOIN weeks w ON w.id = g.week_id
            WHERE w.season_year = :y AND w.week_number = :w
            ORDER BY g.game_time
        """), {"y": season_year, "w": week}).mappings().all()
        out["total_games"] = len(rows)

        for r in rows:
            key = (r["away_team"], r["home_team"])
            ev = es_map.get(key)
            if not ev:
                out["missing_in_espn"].append(f"{r['away_team']} @ {r['home_team']}")
                continue
            seen_keys.add(key)

            # ESPN values
            hs = ev.get("home_score")
            as_ = ev.get("away_score")
            state = (ev.get("state") or "").lower()   # "pre" | "in" | "post"
            win = ev.get("winner")

            # Decide desired status (never regress)
            desired_status = "final" if state == "post" else ("in_progress" if state in ("in", "live") else None)
            need_status = False
            target_status = None
            if desired_status and _rank_status(r["status"]) < _rank_status(desired_status):
                need_status = True
                target_status = "final" if desired_status == "final" else "in_progress"

            # Only update values that are non-NULL and changed
            sets, params = [], {"id": r["id"]}
            if hs is not None and hs != r["home_score"]:
                sets.append("home_score = :hs")
                params["hs"] = hs
            if as_ is not None and as_ != r["away_score"]:
                sets.append("away_score = :as_")
                params["as_"] = as_
            if win and win != r["winner"]:
                sets.append("winner = :winner")
                params["winner"] = win
            if need_status:
                sets.append("status = :st")
                params["st"] = target_status

            if not sets:
                out["matched"] += 1
                continue

            sql = "UPDATE games SET " + ", ".join(sets) + " WHERE id = :id"
            db.session.execute(_text(sql), params)

            if "hs" in params or "as_" in params:
                out["updated_scores"] += 1
            if "winner" in params:
                out["updated_winner"] += 1
            if "st" in params:
                out["updated_status"] += 1

        db.session.commit()

        # Any ESPN events we didn't have in DB (nice-to-know)
        out["unmatched_espn"] = [f"{a} @ {h}" for (a, h) in es_map.keys() - seen_keys]

    return out
def cron_syncscores() -> dict:
    """
    Pick the latest season + latest week in your DB and sync from ESPN.
    Safe to run repeatedly; returns the same summary dict as sync_week_scores_from_espn.
    """
    from sqlalchemy import text as _text
    app = create_app()
    with app.app_context():
        season = db.session.execute(_text("SELECT MAX(season_year) FROM weeks")).scalar()
        if not season:
            logger.warning("cron_syncscores: no season_year in weeks")
            return {"error": "no season_year in weeks"}
        week = db.session.execute(_text(
            "SELECT MAX(week_number) FROM weeks WHERE season_year=:y"
        ), {"y": season}).scalar()
        if not week:
            logger.warning("cron_syncscores: no week_number for season %s", season)
            return {"error": f"no week_number for season {season}"}

    summary = sync_week_scores_from_espn(week, season)
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
    logger.info(f"📩 /start from {username or full_name or first_name or 'unknown'} (chat_id={chat_id})")

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
    from flask_app import create_app, db as _db
    app = create_app()
    with app.app_context():
        # Simple admin check: only allow Tony's Telegram to run this
        is_admin = _db.session.execute(_text("""
            SELECT 1
            FROM participants
            WHERE lower(name)='tony' AND telegram_chat_id = :c
        """), {"c": chat_id}).scalar() is not None
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Find participant by name (case-insensitive)
        pid = _db.session.execute(_text(
            "SELECT id FROM participants WHERE lower(name)=lower(:n)"
        ), {"n": name}).scalar()
        if not pid:
            return await m.reply_text(f'No participant named "{name}" found.')

        # Resolve season for the requested week (latest season containing that week)
        season = _db.session.execute(_text("""
            SELECT season_year
            FROM weeks
            WHERE week_number = :w
            ORDER BY season_year DESC
            LIMIT 1
        """), {"w": week}).scalar()
        if not season:
            return await m.reply_text(f"Week {week} not found in table weeks.")

        # Count existing picks first (for report)
        existing = _db.session.execute(_text("""
            SELECT COUNT(*)
            FROM picks p
            JOIN games g ON g.id = p.game_id
            JOIN weeks w ON w.id = g.week_id
            WHERE p.participant_id = :pid
              AND w.week_number   = :w
              AND w.season_year   = :y
        """), {"pid": pid, "w": week, "y": season}).scalar()

        # Delete picks and report how many were removed
        res = _db.session.execute(_text("""
            DELETE FROM picks p
            USING games g, weeks w
            WHERE p.game_id = g.id
              AND g.week_id = w.id
              AND p.participant_id = :pid
              AND w.week_number    = :w
              AND w.season_year    = :y
            RETURNING p.id
        """), {"pid": pid, "w": week, "y": season})
        deleted = len(res.fetchall())
        _db.session.commit()

    await m.reply_text(
        f'🧹 Deleted {deleted} pick(s) for "{name}" in Week {week} ({season}). '
        f'Previously existed: {existing}.'
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
        is_admin = db.session.execute(_text("""
            SELECT 1 FROM participants WHERE lower(name)='tony' AND telegram_chat_id=:c
        """), {"c": chat_id}).scalar() is not None
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Resolve season if not passed
        if season_year is None:
            season_year = db.session.execute(_text("""
                SELECT season_year FROM weeks
                WHERE week_number=:w
                ORDER BY season_year DESC LIMIT 1
            """), {"w": week}).scalar()
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
        lines.append(f"Missing in ESPN ({len(summary['missing_in_espn'])}): {samp}{' …' if len(summary['missing_in_espn'])>3 else ''}")
    if summary["unmatched_espn"]:
        samp = ", ".join(summary["unmatched_espn"][:3])
        lines.append(f"Extra on ESPN ({len(summary['unmatched_espn'])}): {samp}{' …' if len(summary['unmatched_espn'])>3 else ''}")

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
    from flask_app import create_app, db as _db
    app = create_app()
    with app.app_context():
        # Admin guard: only Tony’s Telegram
        is_admin = _db.session.execute(_text("""
            SELECT 1 FROM participants WHERE lower(name)='tony' AND telegram_chat_id=:c
        """), {"c": chat_id}).scalar() is not None
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Resolve season for that week (latest available)
        season = _db.session.execute(_text("""
            SELECT season_year FROM weeks WHERE week_number=:w ORDER BY season_year DESC LIMIT 1
        """), {"w": week}).scalar()
        if not season:
            return await m.reply_text(f"Week {week} not found in table weeks.")

        # Total games in that week
        total_games = _db.session.execute(_text("""
            SELECT COUNT(*) FROM games g JOIN weeks w ON w.id=g.week_id
            WHERE w.season_year=:y AND w.week_number=:w
        """), {"y": season, "w": week}).scalar()

        # Per-user picked count
        rows = _db.session.execute(_text("""
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
        """), {"y": season, "w": week}).mappings().all()

    # Build summary
    lines = [f"Week {week} ({season}) — total games: {total_games}"]
    for r in rows:
        remaining = (total_games or 0) - int(r["picked"] or 0)
        lines.append(f"• {r['name']}: picked {int(r['picked'] or 0)}/{total_games} — remaining {remaining}")
    await m.reply_text("\n".join(lines))
# /remindweek <week> [participant name...]
# If no name is supplied, nudges everyone with remaining picks.
# Only sends games with g.game_time > now (future), and where no pick exists.
async def remindweek_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    chat_id = str(update.effective_chat.id)

    if not context.args:
        return await m.reply_text('Usage: /remindweek <week_number> [participant name]\n'
                                  'Examples:\n'
                                  '  /remindweek 2\n'
                                  '  /remindweek 2 Kevin\n'
                                  '  /remindweek 2 "Wil Eddie Cano"')

    try:
        week = int(context.args[0])
    except ValueError:
        return await m.reply_text("Week must be an integer, e.g. /remindweek 2")

    name = " ".join(context.args[1:]).strip().strip('"').strip("'") if len(context.args) > 1 else None

    from flask_app import create_app, db as _db
    app = create_app()
    now_cutoff = _now_utc_naive()

    with app.app_context():
        # Admin guard
        is_admin = _db.session.execute(_text("""
            SELECT 1 FROM participants WHERE lower(name)='tony' AND telegram_chat_id=:c
        """), {"c": chat_id}).scalar() is not None
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Resolve season
        season = _db.session.execute(_text("""
            SELECT season_year FROM weeks WHERE week_number=:w ORDER BY season_year DESC LIMIT 1
        """), {"w": week}).scalar()
        if not season:
            return await m.reply_text(f"Week {week} not found in table weeks.")

        # Decide target participants
        if name:
            targets = _db.session.execute(_text("""
                SELECT id, name, telegram_chat_id
                FROM participants
                WHERE lower(name)=lower(:n)
            """), {"n": name}).mappings().all()
            if not targets:
                return await m.reply_text(f'No participant named "{name}" found.')
        else:
            # Everyone with remaining unpicked games
            targets = _db.session.execute(_text("""
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
            """), {"y": season, "w": week}).mappings().all()

        sent_total = 0
        for u in targets:
            if not u["telegram_chat_id"]:
                continue  # cannot DM

            # Unpicked, future games only
            rows = _db.session.execute(_text("""
                SELECT g.id AS game_id, g.away_team, g.home_team, g.game_time
                FROM games g
                JOIN weeks w ON w.id=g.week_id
                LEFT JOIN picks p ON p.game_id=g.id AND p.participant_id=:pid
                WHERE w.season_year=:y AND w.week_number=:w
                  AND (p.id IS NULL OR p.selected_team IS NULL)
                  AND (g.game_time IS NULL OR g.game_time > :now)  -- future only
                ORDER BY g.game_time NULLS LAST, g.id
            """), {"pid": u["id"], "y": season, "w": week, "now": now_cutoff}).mappings().all()

            if not rows:
                # Optionally let them know they’re all set / or only past games remain
                _send_message(u["telegram_chat_id"], f"✅ {u['name']}: you’re all set for Week {week} ({season}).")
                continue

            # Send one message per game with two buttons
            for r in rows:
                kb = {
                    "inline_keyboard": [
                        [{"text": r["away_team"], "callback_data": f"pick:{r['game_id']}:{r['away_team']}"}],
                        [{"text": r["home_team"], "callback_data": f"pick:{r['game_id']}:{r['home_team']}"}],
                    ]
                }
                _send_message(u["telegram_chat_id"], f"{r['away_team']} @ {r['home_team']}", reply_markup=kb)
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

    broadcast = (len(args) > 1 and args[1].lower() == "all")

    from flask_app import create_app, db as _db
    app = create_app()
    with app.app_context():
        # Admin guard (only Tony's Telegram chat may invoke)
        is_admin = _db.session.execute(_text("""
            SELECT 1 FROM participants WHERE lower(name)='tony' AND telegram_chat_id=:c
        """), {"c": chat_id}).scalar() is not None
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Resolve season for this week (latest)
        season = _db.session.execute(_text("""
            SELECT season_year FROM weeks
            WHERE week_number=:w
            ORDER BY season_year DESC
            LIMIT 1
        """), {"w": week}).scalar()
        if not season:
            return await m.reply_text(f"Week {week} not found in table weeks.")

                # Completed games in this week (explicit winner OR both scores set and not a tie)
        total_completed = _db.session.execute(_text("""
            SELECT COUNT(*)
            FROM games g
            JOIN weeks w ON w.id = g.week_id
            WHERE w.season_year=:y
              AND w.week_number=:w
              AND (
                    g.winner IS NOT NULL
                 OR (g.home_score IS NOT NULL AND g.away_score IS NOT NULL AND g.home_score <> g.away_score)
              )
        """), {"y": season, "w": week}).scalar() or 0

        if total_completed == 0:
            return await m.reply_text(f"No games completed yet for Week {week} ({season}).")

        # Per-participant wins/losses for completed games
        rows = _db.session.execute(_text("""
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
        """), {"y": season, "w": week}).mappings().all()

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
async def seasonboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      /seasonboard [all]

    Shows a season-to-date scoreboard: per-week wins per participant and total,
    ordered by total wins. Includes only completed games (games with a winner).
    If 'all' is provided, DM the same board to every participant.
    """
    m = update.effective_message
    chat_id = str(update.effective_chat.id)
    args = context.args or []
    broadcast = (len(args) >= 1 and args[0].lower() == "all")

    from flask_app import create_app, db as _db
    app = create_app()
    with app.app_context():
        # Admin guard
        is_admin = _db.session.execute(_text("""
            SELECT 1 FROM participants WHERE lower(name)='tony' AND telegram_chat_id=:c
        """), {"c": chat_id}).scalar() is not None
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Latest season
        season = _db.session.execute(_text("""
            SELECT MAX(season_year) FROM weeks
        """)).scalar()
        if not season:
            return await m.reply_text("No season data found in weeks table.")

        # All week numbers in season (ordered)
        weeks = [r[0] for r in _db.session.execute(_text("""
            SELECT DISTINCT week_number
            FROM weeks
            WHERE season_year=:y
            ORDER BY week_number
        """), {"y": season}).fetchall()]
        if not weeks:
            return await m.reply_text(f"No weeks found for season {season}.")

        # Compute wins per participant per week for completed games
        rows = _db.session.execute(_text("""
            WITH wg AS (
              SELECT
                w.week_number,
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
                AND (
                     g.winner IS NOT NULL
                  OR (g.home_score IS NOT NULL AND g.away_score IS NOT NULL AND g.home_score <> g.away_score)
                )
            )
            SELECT u.id,
                   u.name,
                   u.telegram_chat_id,
                   wg.week_number,
                   COALESCE(SUM(CASE WHEN p.selected_team = wg.winner THEN 1 ELSE 0 END), 0) AS wins
            FROM participants u
            LEFT JOIN picks p ON p.participant_id = u.id
            LEFT JOIN wg ON wg.id = p.game_id
            GROUP BY u.id, u.name, u.telegram_chat_id, wg.week_number
            ORDER BY u.name, wg.week_number
        """), {"y": season}).mappings().all()

        # Organize into dict: name -> {week_number: wins}
        pdata = {}
        chat_ids = {}
        for r in rows:
            name = r["name"]
            if name not in pdata:
                pdata[name] = {}
                chat_ids[name] = r["telegram_chat_id"]
            wk = r["week_number"]
            if wk is not None:
                pdata[name][wk] = int(r["wins"])

        # Ensure all participants appear even if they have no completed wins yet
        missing_participants = _db.session.execute(_text("""
            SELECT id, name, telegram_chat_id FROM participants
        """)).mappings().all()
        for rp in missing_participants:
            pdata.setdefault(rp["name"], {})
            chat_ids.setdefault(rp["name"], rp["telegram_chat_id"])

        # Build table lines
        names = sorted(pdata.keys())
        totals = {n: sum(pdata[n].get(w, 0) for w in weeks) for n in names}
        names.sort(key=lambda n: (-totals[n], n))  # by total desc, then name

        header = "🏆 Season-to-date Scoreboard"
        sub = f"Season {season} — completed games only"
        col_wks = " ".join(f"W{w}" for w in weeks)
        title = f"{header}\n{sub}\n\nName   {col_wks}  | Total"

        lines = [title]
        for n in names:
            wk_counts = [str(pdata[n].get(w, 0)) for w in weeks]
            line = f"{n}: " + " ".join(wk_counts) + f"  | {totals[n]}"
            lines.append(line)

        body = "\n".join(lines)

        await m.reply_text(body)

        if broadcast:
            sent = 0
            for n in names:
                cid = chat_ids.get(n)
                if cid:
                    try:
                        _send_message(cid, body)
                        sent += 1
                    except Exception:
                        logger.exception("Failed sending /seasonboard to %s", n)
            await m.reply_text(f"✅ Sent season board to {sent} participant(s).")


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
        return await m.reply_text("Usage: /seepicks <week_number> <participant|all>\nExample: /seepicks 3 all")

    # Parse week
    try:
        week = int(args[0])
    except ValueError:
        return await m.reply_text("Week must be an integer, e.g. /seepicks 3 all")

    target = " ".join(args[1:]).strip().strip('"').strip("'")  # allow spaces/quotes in names
    is_all = target.lower() == "all"

    from flask_app import create_app, db as _db
    app = create_app()
    with app.app_context():
        # Admin guard (only Tony's Telegram can run this)
        is_admin = _db.session.execute(_text("""
            SELECT 1 FROM participants WHERE lower(name)='tony' AND telegram_chat_id=:c
        """), {"c": chat_id}).scalar() is not None
        if not is_admin:
            return await m.reply_text("Sorry, this command is restricted.")

        # Latest season that has this week
        season = _db.session.execute(_text("""
            SELECT season_year FROM weeks WHERE week_number=:w ORDER BY season_year DESC LIMIT 1
        """), {"w": week}).scalar()
        if not season:
            return await m.reply_text(f"Week {week} not found in table weeks.")

        # Games in the week
        games = _db.session.execute(_text("""
            SELECT g.id, g.away_team, g.home_team
            FROM games g
            JOIN weeks w ON w.id = g.week_id
            WHERE w.season_year=:y AND w.week_number=:w
            ORDER BY g.game_time NULLS LAST, g.id
        """), {"y": season, "w": week}).mappings().all()
        if not games:
            return await m.reply_text(f"No games found for Week {week} ({season}).")

        # Participants scope
        if is_all:
            participants = _db.session.execute(_text("""
                SELECT id, name, telegram_chat_id
                FROM participants
                ORDER BY name
            """)).mappings().all()
        else:
            row = _db.session.execute(_text("""
                SELECT id, name, telegram_chat_id
                FROM participants
                WHERE lower(name)=lower(:name)
                LIMIT 1
            """), {"name": target}).mappings().first()
            if not row:
                return await m.reply_text(f'Participant "{target}" not found.')
            participants = [row]

        if not participants:
            return await m.reply_text("No participants found.")

        # Picks map (participant_id, game_id) -> selected_team
        picks = _db.session.execute(_text("""
            SELECT p.participant_id, p.game_id, p.selected_team
            FROM picks p
            WHERE p.game_id IN (
                SELECT g.id
                FROM games g JOIN weeks w ON w.id=g.week_id
                WHERE w.season_year=:y AND w.week_number=:w
            )
        """), {"y": season, "w": week}).mappings().all()

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

    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes  # local import to avoid import-time failures
    from telegram import Update

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_pick))
    application.add_handler(CommandHandler("sendweek", sendweek_command))
    application.add_handler(CommandHandler("syncscores", syncscores_command))
    application.add_handler(CommandHandler("getscores", getscores_command))
    application.add_handler(CommandHandler("seasonboard", seasonboard_command))
    application.add_handler(CommandHandler("deletepicks", deletepicks_command))
    application.add_handler(CommandHandler("whoisleft", whoisleft_command))
    application.add_handler(CommandHandler("seepicks", seepicks_command))
    application.add_handler(CommandHandler("remindweek", remindweek_command))
    application.run_polling()
def _send_message(chat_id: str, text: str, reply_markup: dict | None = None):
    """Low-level helper to send a message via Telegram HTTP API (sync call)."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    data = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        # Accept either a dict (encode) or a pre-encoded JSON string
        data["reply_markup"] = (
            reply_markup if isinstance(reply_markup, str) else json.dumps(reply_markup)
        )
    with httpx.Client(timeout=20) as client:
        resp = client.post(f"{TELEGRAM_API_URL}/sendMessage", data=data)
        resp.raise_for_status()

def send_week_games(week_number: int, season_year: int):
    """Send Week games with inline buttons to all participants who have telegram_chat_id."""
    app = create_app()
    with app.app_context():
        week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()
        if not week:
            logger.error(f"❌ No week found for {season_year} W{week_number}")
            return

        games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()
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
                text = f"{g.away_team} @ {g.home_team}\n{_pt(g.game_time)}"
                try:
                    _send_message(chat_id, text, reply_markup=kb)
                    logger.info(f"✅ Sent game to {part.name}: {g.away_team} @ {g.home_team}")
                except Exception as e:
                    logger.exception("❌ Failed to send game message: %s", e)

# --- /sendweek admin command (additive, with DRY and ME) ---
async def sendweek_command(update, context):
    """
    Usage:
      /sendweek <week>            -> send to ALL (existing behavior)
      /sendweek <week> dry        -> DRY-RUN (no sends; report counts)
      /sendweek <week> me         -> send ONLY to the caller (admin)
      /sendweek <week> <name...>  -> send ONLY to that participant by name
    """
    import asyncio
    from sqlalchemy import text

    user = update.effective_user
    if ADMIN_IDS and (not user or user.id not in ADMIN_IDS):
        if update.message:
            await update.message.reply_text("Sorry, admin only.")
        return

    args = context.args or []
    if not args or not args[0].isdigit():
        if update.message:
            await update.message.reply_text("Usage: /sendweek <week_number> [dry|me|<participant name>]")
        return
    week_number = int(args[0])
    target = "all" if len(args) == 1 else " ".join(args[1:]).strip()

    # Helper: send to a single participant id/chat, only unpicked for that week
    def _send_to_one(participant_id: int, chat_id: str, season_year: int):
        # get only unpicked games for this participant
        rows = db.session.execute(text("""
            select g.id, g.away_team, g.home_team
            from games g
            join weeks w on w.id=g.week_id
            left join picks p on p.game_id=g.id and p.participant_id=:pid
            where w.season_year=:y and w.week_number=:w
              and (p.id is null or p.selected_team is null)
            order by g.game_time nulls last, g.id
        """), {"pid": participant_id, "y": season_year, "w": week_number}).mappings().all()

        sent = 0
        for g in rows:
            kb = {
                "inline_keyboard": [
                    [{"text": g["away_team"], "callback_data": f"pick:{g['id']}:{g['away_team']}"}],
                    [{"text": g["home_team"], "callback_data": f"pick:{g['id']}:{g['home_team']}"}],
                ]
            }
            _send_message(str(chat_id), f"{g['away_team']} @ {g['home_team']}", reply_markup=kb)
            sent += 1
        return sent

    # Targeted modes (dry/me/name) should NOT auto-create weeks; only use existing week.
    def _find_existing_week():
        return Week.query.filter_by(week_number=week_number).order_by(Week.season_year.desc()).first()

    # Handle targeted modes inline; keep broadcast in a background thread
    if target.lower() in ("dry", "me") or target.lower() not in ("all",):
        app = create_app()
        with app.app_context():
            wk = _find_existing_week()
            if not wk:
                if update.message:
                    await update.message.reply_text(f"Week {week_number} not found yet. (Dry/me/name modes do not auto-create.)")
                return
            season_year = wk.season_year

            if target.lower() == "dry":
                # Count how many messages would be sent to all registered participants
                people = db.session.execute(text("""
                    select id, name, telegram_chat_id
                    from participants
                    where telegram_chat_id is not null
                """)).mappings().all()
                total_msgs = 0
                for u in people:
                    cnt = db.session.execute(text("""
                        select count(*)
                        from games g
                        join weeks w on w.id=g.week_id
                        left join picks p on p.game_id=g.id and p.participant_id=:pid
                        where w.season_year=:y and w.week_number=:w
                          and (p.id is null or p.selected_team is null)
                    """), {"pid": u["id"], "y": season_year, "w": week_number}).scalar()
                    total_msgs += int(cnt or 0)
                await update.message.reply_text(
                    f"DRY RUN: would send {total_msgs} button message(s) to {len(people)} participant(s) for Week {week_number} ({season_year})."
                )
                return

            if target.lower() == "me":
                me_chat = str(update.effective_chat.id)
                person = db.session.execute(text("""
                    select id, telegram_chat_id from participants
                    where telegram_chat_id = :c
                """), {"c": me_chat}).mappings().first()
                if not person:
                    await update.message.reply_text("You're not linked yet. Send /start first.")
                    return
                sent = _send_to_one(person["id"], person["telegram_chat_id"], season_year)
                await update.message.reply_text(f"✅ Sent {sent} unpicked game(s) for Week {week_number} to you.")
                return

            # Otherwise: treat target as a participant name
            name = target
            person = db.session.execute(text("""
                select id, name, telegram_chat_id from participants
                where lower(name)=lower(:n)
            """), {"n": name}).mappings().first()
            if not person:
                await update.message.reply_text(f"Participant '{name}' not found.")
                return
            if not person["telegram_chat_id"]:
                await update.message.reply_text(f"Participant '{name}' has no Telegram chat linked. Ask them to /start.")
                return
            sent = _send_to_one(person["id"], person["telegram_chat_id"], season_year)
            await update.message.reply_text(f"✅ Sent {sent} unpicked game(s) for Week {week_number} to {person['name']}.")
            return

    # Default: broadcast to ALL (unchanged behavior; may create the week if missing)
    async def _do_broadcast():
        app = create_app()
        with app.app_context():
            wk = Week.query.filter_by(week_number=week_number).order_by(Week.season_year.desc()).first()
            if not wk:
                # best-effort create if missing
                import datetime as dt
                from nfl_data import fetch_and_create_week
                season_year = dt.datetime.utcnow().year
                fetch_and_create_week(week_number, season_year)
                wk = Week.query.filter_by(week_number=week_number).order_by(Week.season_year.desc()).first()
            season_year = wk.season_year
            send_week_games(week_number=week_number, season_year=season_year)

    if update.message:
        await update.message.reply_text(f"Sending Week {week_number} to all registered participants…")
    await asyncio.to_thread(_do_broadcast)
    if update.message:
        await update.message.reply_text("✅ Done.")
if __name__ == "__main__":
    import sys, json
    if len(sys.argv) >= 2 and sys.argv[1] == "cron":
        print(json.dumps(cron_syncscores()))
    elif len(sys.argv) >= 3 and sys.argv[1] == "syncscores":
        try:
            week = int(sys.argv[2])
        except Exception:
            raise SystemExit("Usage: python jobs.py syncscores <week> [season_year]")
        season = int(sys.argv[3]) if len(sys.argv) >= 4 else None
        if season is None:
            from sqlalchemy import text as _text
            app = create_app()
            with app.app_context():
                season = db.session.execute(_text("""
                    SELECT season_year FROM weeks WHERE week_number=:w
                    ORDER BY season_year DESC LIMIT 1
                """), {"w": week}).scalar()
                if not season:
                    raise SystemExit(f"Week {week} not found.")
        print(json.dumps(sync_week_scores_from_espn(week, season)))


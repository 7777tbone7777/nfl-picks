# bot/telegram_handlers.py
from __future__ import annotations

import os
import json
import asyncio
import logging
from typing import Any, Dict, List, Optional

from telegram import Update
from telegram.ext import ContextTypes

# Re-export command handlers that live in jobs.py
from bot.jobs import (
    handle_pick,
    start,
    sendweek_command,
    syncscores_command,
    getscores_command,
    seasonboard_command,
    deletepicks_command,
    whoisleft_command,
    seepicks_command,
    remindweek_command,
)

# DB access for /mypicks
from models import db  # type: ignore
from sqlalchemy import text

log = logging.getLogger(__name__)

# --- /admin command with subcommands ----------------------------------------

# ADMIN_IDS already used elsewhere in your app; re-derive here from env
ADMIN_IDS = {int(x) for x in (os.getenv("ADMIN_IDS") or "").split(",") if x.strip().isdigit()}

def _is_admin(user) -> bool:
    try:
        return bool(ADMIN_IDS) and user and (user.id in ADMIN_IDS)
    except Exception:
        return False

def _parse_admin_args(text: str):
    # "/admin <subcommand> [args...]"
    parts = (text or "").strip().split()
    sub = parts[1].lower() if len(parts) >= 2 else ""
    rest = parts[2:] if len(parts) >= 3 else []
    return sub, rest

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /admin participants
    /admin remove <id|name...>
    /admin sendweek upcoming
    /admin import upcoming
    /admin winners
    """
    user = update.effective_user
    if not _is_admin(user):
        if update.message:
            await update.message.reply_text("Sorry, admin only.")
        return

    if not update.message or not update.message.text:
        await update.message.reply_text("Usage: /admin <participants|remove|sendweek|import|winners> [...]")
        return

    sub, rest = _parse_admin_args(update.message.text)

    if sub == "participants":
        # List id | name | chat
        from bot.jobs import create_app, db  # lazy import avoids cycles
        from sqlalchemy import text as T
        app = create_app()
        with app.app_context():
            rows = db.session.execute(
                T("SELECT id, name, COALESCE(telegram_chat_id,'') AS chat FROM participants ORDER BY id")
            ).mappings().all()
        lines = [f"{r['id']:>4} | {r['name']} | chat={r['chat']}" for r in rows]
        await update.message.reply_text("Participants:\n" + ("\n".join(lines) if lines else "(none)"))
        return

    if sub == "remove":
        if not rest:
            await update.message.reply_text("Usage: /admin remove <id|name...>")
            return
        target = " ".join(rest).strip()
        from bot.jobs import create_app, db
        from sqlalchemy import text as T
        app = create_app()
        with app.app_context():
            if target.isdigit():
                pid = int(target)
                row = db.session.execute(T("SELECT id,name FROM participants WHERE id=:pid"), {"pid": pid}).mappings().first()
                if not row:
                    await update.message.reply_text(f"ID {pid} not found.")
                    return
                db.session.execute(T("DELETE FROM picks WHERE participant_id=:pid"), {"pid": pid})
                db.session.execute(T("DELETE FROM participants WHERE id=:pid"), {"pid": pid})
                db.session.commit()
                await update.message.reply_text(f"Deleted {row['name']} (id={pid}) and their picks.")
            else:
                row = db.session.execute(
                    T("SELECT id,name FROM participants WHERE lower(name)=lower(:n)"),
                    {"n": target}
                ).mappings().first()
                if not row:
                    await update.message.reply_text(f"Participant '{target}' not found.")
                    return
                pid = int(row["id"])
                db.session.execute(T("DELETE FROM picks WHERE participant_id=:pid"), {"pid": pid})
                db.session.execute(T("DELETE FROM participants WHERE id=:pid"), {"pid": pid})
                db.session.commit()
                await update.message.reply_text(f"Deleted {row['name']} (id={pid}) and their picks.")
        return

    if sub == "deletepicks":
        # Usage: /admin deletepicks <id|name...> <week_number> [season_year] [dry]
        if len(rest) < 2:
            await update.message.reply_text(
                "Usage: /admin deletepicks <id|name...> <week_number> [season_year] [dry]"
            )
            return

        # Parse arguments
        # Everything up to the last 1–2 tokens is the name/id; the last numeric(s) are week[/season]
        tail = rest[-2:]  # could be [week, season] or [week, 'dry'] etc.
        head = rest[:-2] if len(rest) > 2 else rest[:-1]  # name/id pieces

        # Find numeric tokens at the end
        nums = [x for x in tail if x.isdigit()]
        flags = [x for x in tail if not x.isdigit()]
        dry = any(x.lower() == "dry" for x in flags)

        # If only one number was provided, it's the week; season is None (we'll default to latest)
        if not nums:
            await update.message.reply_text(
                "Missing week number. Usage: /admin deletepicks <id|name...> <week_number> [season_year] [dry]"
            )
            return
        if len(nums) == 1:
            week_number = int(nums[0])
            target_name_or_id = " ".join(head + ([x for x in tail if x == nums[0]] and [])) or rest[0]
            season_year = None
        else:
            # two numbers: week then season (order-agnostic if you want, but we’ll assume week first)
            week_number = int(nums[0])
            season_year = int(nums[1])
            # target is everything before those two numbers
            target_name_or_id = " ".join(head).strip() or rest[0]

        from bot.jobs import create_app, db
        from sqlalchemy import text as T
        app = create_app()
        with app.app_context():
            # Resolve participant id
            pid = None
            if target_name_or_id.isdigit():
                pid = db.session.execute(
                    T("SELECT id FROM participants WHERE id=:pid"), {"pid": int(target_name_or_id)}
                ).scalar()
                pname = db.session.execute(
                    T("SELECT name FROM participants WHERE id=:pid"), {"pid": int(target_name_or_id)}
                ).scalar()
            else:
                row = db.session.execute(
                    T("SELECT id, name FROM participants WHERE lower(name)=lower(:n)"),
                    {"n": target_name_or_id},
                ).mappings().first()
                pid = row["id"] if row else None
                pname = row["name"] if row else None

            if not pid:
                await update.message.reply_text(f"Participant '{target_name_or_id}' not found.")
                return

            # Resolve season if not provided
            if season_year is None:
                season_year = db.session.execute(
                    T("SELECT MAX(season_year) FROM weeks")
                ).scalar()

            # Count picks for that participant & week
            cnt = db.session.execute(
                T("""
                   SELECT COUNT(*) FROM picks p
                   JOIN games g ON g.id = p.game_id
                   JOIN weeks w ON w.id = g.week_id
                   WHERE p.participant_id = :pid
                     AND w.season_year = :y
                     AND w.week_number = :w
                """),
                {"pid": int(pid), "y": int(season_year), "w": int(week_number)},
            ).scalar() or 0

            if dry:
                await update.message.reply_text(
                    f"[DRY RUN] {pname or pid}: would delete {cnt} pick(s) for Week {week_number} ({season_year})."
                )
                return

            # Delete them
            db.session.execute(
                T("""
                   DELETE FROM picks
                   USING games g, weeks w
                   WHERE picks.game_id = g.id
                     AND w.id = g.week_id
                     AND picks.participant_id = :pid
                     AND w.season_year = :y
                     AND w.week_number = :w
                """),
                {"pid": int(pid), "y": int(season_year), "w": int(week_number)},
            )
            db.session.commit()

        await update.message.reply_text(
            f"Deleted {cnt} pick(s) for {pname or pid} in Week {week_number} ({season_year})."
        )
        return

    if sub == "sendweek" and rest[:1] == ["upcoming"]:
        from importlib import import_module
        jobs = import_module("bot.jobs")
        res = jobs.cron_send_upcoming_week()
        await update.message.reply_text("sendweek_upcoming:\n" + json.dumps(res, default=str, indent=2))
        return

    if sub == "import" and rest[:1] == ["upcoming"]:
        from importlib import import_module
        jobs = import_module("bot.jobs")
        res = jobs.cron_import_upcoming_week()
        await update.message.reply_text("import-week-upcoming:\n" + json.dumps(res, default=str, indent=2))
        return

    if sub == "winners":
        from importlib import import_module
        jobs = import_module("bot.jobs")
        res = jobs.cron_announce_weekly_winners()
        await update.message.reply_text("announce-winners:\n" + json.dumps(res, default=str, indent=2))
        return

    await update.message.reply_text("Usage: /admin <participants|remove|sendweek upcoming|import upcoming|winners>")

# ---------- helpers for /mypicks ----------

def _format_user_picks(picks: List[Dict[str, Any]]) -> str:
    """Format a user's picks into a readable message."""
    if not picks:
        return "You have no saved picks yet."
    lines = []
    for p in picks:
        week_no = p.get("week_number", "?")
        away = p.get("away_team", "?")
        home = p.get("home_team", "?")
        choice = p.get("selected_team", "?")
        lines.append(f"• Week {week_no} — {away} @ {home} → {choice}")
    return "\n".join(lines)


def _fetch_picks_sync(telegram_user_id: Optional[int]) -> List[Dict[str, Any]]:
    """
    Blocking DB work — executed via asyncio.to_thread() from the async handler.
    Returns dicts with: week_number, away_team, home_team, selected_team.
    """
    if telegram_user_id is None:
        return []

    telegram_chat_id = str(telegram_user_id)

    with db.engine.connect() as conn:  # type: ignore[attr-defined]
        # 1) Find participant by telegram_chat_id
        part_row = conn.execute(
            text(
                """
                SELECT id
                FROM participants
                WHERE telegram_chat_id = :tid
                LIMIT 1
                """
            ),
            {"tid": telegram_chat_id},
        ).fetchone()
        if not part_row:
            return []

        participant_id = part_row[0]

        # 2) Join picks → games → weeks
        rows = conn.execute(
            text(
                """
                SELECT
                  w.week_number,
                  g.away_team,
                  g.home_team,
                  p.selected_team
                FROM picks p
                JOIN games g ON g.id = p.game_id
                JOIN weeks w ON w.id = g.week_id
                WHERE p.participant_id = :pid
                ORDER BY w.season_year DESC, w.week_number ASC, g.game_time ASC
                """
            ),
            {"pid": participant_id},
        ).fetchall()

        picks: List[Dict[str, Any]] = []
        for r in rows:
            picks.append(
                {
                    "week_number": r[0],
                    "away_team": r[1],
                    "home_team": r[2],
                    "selected_team": r[3],
                }
            )
        return picks


async def _load_user_picks(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> List[Dict[str, Any]]:
    """
    Preferred path: use an injected service at application.bot_data['svc'].get_user_picks(user_id).
    Fallback: run the direct DB query in a thread to avoid blocking PTB's event loop.
    """
    user_id = getattr(update.effective_user, "id", None)

    # Service-first (optional)
    svc = None
    if hasattr(context, "application") and hasattr(context.application, "bot_data"):
        svc = context.application.bot_data.get("svc")
    if svc and hasattr(svc, "get_user_picks"):
        result = svc.get_user_picks(user_id)
        return await result if hasattr(result, "__await__") else result

    # Fallback to direct DB, offloaded to a thread
    return await asyncio.to_thread(_fetch_picks_sync, user_id)


# ---------- /mypicks (lives here) ----------

async def mypicks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /mypicks — show the requesting user's saved picks.

    - Early stub reply proves wiring
    - Async-safe DB access (service-injected or to_thread)
    """
    msg = update.effective_message or update.message
    try:
        log.info(
            "mypicks: entered handler user_id=%s chat_id=%s",
            getattr(update.effective_user, "id", None),
            getattr(update.effective_chat, "id", None),
        )

        if msg:
            await msg.reply_text("✅ /mypicks handler reached. Fetching your picks...")

        picks = await _load_user_picks(update, context)
        out_text = _format_user_picks(picks)

        if msg:
            await msg.reply_text(out_text, disable_web_page_preview=True)

    except Exception as e:  # pragma: no cover
        log.exception("mypicks: crashed: %s", e)
        if msg:
            try:
                await msg.reply_text("❌ Sorry, /mypicks failed. Check logs.")
            except Exception:
                pass


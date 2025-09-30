# bot/telegram_handlers.py
from __future__ import annotations

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


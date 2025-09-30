# bot/telegram_handlers.py
from __future__ import annotations

import logging
from typing import Any, Dict, List

from telegram import Update
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)


def _format_user_picks(picks: List[Dict[str, Any]]) -> str:
    """Format a user's picks into a readable message."""
    if not picks:
        return "You have no saved picks yet."
    lines = []
    for p in picks:
        week = p.get("week", "?")
        game = p.get("game") or f"{p.get('away','?')} @ {p.get('home','?')}"
        choice = p.get("pick", "?")
        lines.append(f"• Week {week} — {game} → {choice}")
    return "\n".join(lines)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all for unknown /commands (non-blocking)."""
    msg = update.effective_message
    if msg:
        await msg.reply_text("Sorry, I don’t recognize that command.")


async def mypicks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mypicks — reply with the user's saved picks.
    This is PTB v20+ safe (async, awaits replies) and logs clearly.
    """
    msg = update.effective_message or update.message
    try:
        log.info(
            "mypicks: entered handler user_id=%s chat_id=%s",
            getattr(update.effective_user, "id", None),
            getattr(update.effective_chat, "id", None),
        )

        # Prove wiring early
        if msg:
            await msg.reply_text("✅ /mypicks handler reached. Fetching your picks...")

        # ----- Business logic hook -----
        # Prefer a service bound in Application.bot_data (async-friendly).
        picks: List[Dict[str, Any]] = []
        user_id = getattr(update.effective_user, "id", None)

        svc = (
            getattr(context.application, "bot_data", {}).get("svc")
            if hasattr(context.application, "bot_data")
            else None
        )
        if svc and hasattr(svc, "get_user_picks"):

            maybe_coro = svc.get_user_picks(user_id)
            if hasattr(maybe_coro, "__await__"):
                picks = await maybe_coro  # async service
            else:
                picks = maybe_coro  # sync service

        # If no service is provided, keep the empty default (no saved picks).
        text = _format_user_picks(picks)
        if msg:
            await msg.reply_text(text, disable_web_page_preview=True)

    except Exception as e:
        log.exception("mypicks: crashed: %s", e)
        if msg:
            try:
                await msg.reply_text("❌ Sorry, /mypicks failed. Check logs.")
            except Exception:
                pass

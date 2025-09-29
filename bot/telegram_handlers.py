from __future__ import annotations

import logging
from typing import Optional

# Import your db and models exactly as in your app
# Adjust these two imports if your project structure is different.
from app import db  # `db = SQLAlchemy()` from your Flask app
from telegram import Update
from telegram.ext import ContextTypes

from models import Participant  # your SQLAlchemy model

logger = logging.getLogger(__name__)

# If you want to use the Flask app object directly in places (optional)
_FLASK_APP = None


def init_app(app) -> None:
    """Optionally called by bot_runner to pass in the Flask app (not required when using the wrapper)."""
    global _FLASK_APP
    _FLASK_APP = app


# ------------- Handlers ------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start — Registers the Telegram chat to an existing (or new) Participant record.
    NOTE: This function expects to be called inside a Flask app_context()
    (bot_runner wraps it), so db.session/Model.query will work.
    """
    chat = update.effective_chat
    user = update.effective_user
    chat_id = chat.id if chat else None

    if chat_id is None:
        return

    try:
        # Look up by telegram_chat_id; create if missing (safe if your schema allows)
        p = Participant.query.filter_by(telegram_chat_id=chat_id).first()
        if p is None:
            p = Participant(
                telegram_chat_id=chat_id,
                telegram_username=(user.username if user else None),
                display_name=(user.full_name if user else None),
            )
            db.session.add(p)
            db.session.commit()
            created = True
        else:
            # Keep username/display_name fresh
            if user:
                p.telegram_username = user.username
                p.display_name = user.full_name
                db.session.commit()
            created = False

        text = (
            "🎉 You’re all set! I’ll use this chat for your NFL picks."
            if created
            else "✅ You're already registered. Use /mypicks to see your selections."
        )
        await context.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:  # noqa: BLE001
        logger.exception("Error in /start handler")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Couldn't register this chat due to an internal error.\n({type(e).__name__})",
        )


async def mypicks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /mypicks — Show this user's picks.
    Replace the demo query with your actual schema/logic if different.
    Expects to run inside a Flask app_context() (provided by bot_runner).
    """
    chat = update.effective_chat
    chat_id = chat.id if chat else None
    if chat_id is None:
        return

    try:
        # One way: join via Participant if you store picks per participant.
        participant = Participant.query.filter_by(telegram_chat_id=chat_id).first()
        if participant is None:
            await context.bot.send_message(
                chat_id=chat_id,
                text="I don’t have you registered yet. Run /start first.",
            )
            return

        # --- Replace this block with your real query ---
        # Example using raw SQL if you had picks in a table "picks" keyed by participant_id.
        # row = db.session.execute(
        #     text(\"\"\"\n
        #     SELECT json_agg(p ORDER BY p.week) AS picks
        #     FROM picks p
        #     WHERE p.participant_id = :pid
        #     \"\"\"), {"pid": participant.id}
        # ).first()
        #
        # Instead, we’ll just show a friendly placeholder if you haven’t wired it up yet.
        # ------------------------------------------------

        # Placeholder message (non-crashing)
        await context.bot.send_message(
            chat_id=chat_id,
            text="📋 Your picks feature is enabled. (Add your real query in telegram_handlers.py → mypicks.)",
        )

    except Exception as e:  # noqa: BLE001
        logger.exception("Error in /mypicks handler")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Couldn't load your picks due to an internal error.\n({type(e).__name__})",
        )

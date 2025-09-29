# bot/telegram_handlers.py
from __future__ import annotations

import logging
from datetime import datetime, timezone

from extensions import db  # your Flask-SQLAlchemy db instance
from flask import current_app
from sqlalchemy import text
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from models import Participant  # your Flask-SQLAlchemy model

log = logging.getLogger(__name__)


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zero-DB sanity check."""
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    await update.effective_chat.send_message(f"pong 🏈 ({when})")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    # Using Flask-SQLAlchemy ORM inside app_context (the wrapper in bot_runner guarantees it)
    p = Participant.query.filter_by(telegram_chat_id=str(chat_id)).first()
    if not p:
        msg = (
            "Hey! I don’t recognize this chat yet.\n\n"
            "Please register on the site first, then run /mypicks."
        )
        await update.message.reply_text(msg)
        return

    await update.message.reply_text(
        f"Welcome back, {p.display_name or p.name}! Try /mypicks to see your picks.",
        parse_mode=ParseMode.HTML,
    )


async def mypicks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    rows = db.session.execute(
        text(
            """
            select u.display_name, pu.week, pu.team_abbr
            from pick_user pu
            join participant u on u.id = pu.participant_id
            where u.telegram_chat_id = :chat_id
            order by pu.week
            """
        ),
        {"chat_id": chat_id},
    ).fetchall()

    if not rows:
        await update.message.reply_text("No picks found yet for this chat.")
        return

    header_name = rows[0].display_name
    lines = [f"<b>{header_name}</b>"]
    for r in rows:
        lines.append(f"Week {r.week}: {r.team_abbr}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    log.info("Fallback text from %s: %r", update.effective_user.id, txt)
    await update.message.reply_text("Try /start or /mypicks.")

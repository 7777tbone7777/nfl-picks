from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from db import db  # SQLAlchemy session scoped in your project
from sqlalchemy import text
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from utils.time_utils import now_utc  # helper already in repo (returns aware UTC datetime)

# ---------- helpers ----------


def _get_or_create_participant(telegram_chat_id: str, name: Optional[str]) -> int:
    """
    Returns participant.id for this telegram_chat_id, creating one if missing.
    """
    # name can be None; DB requires name NOT NULL, so default to the chat id string
    fallback_name = name or f"tg_{telegram_chat_id}"
    row = db.session.execute(
        text(
            """
            SELECT id FROM participants
            WHERE telegram_chat_id = :chat_id
            LIMIT 1
            """
        ),
        {"chat_id": str(telegram_chat_id)},
    ).first()

    if row:
        return row[0]

    # create participant
    created = db.session.execute(
        text(
            """
            INSERT INTO participants (name, telegram_chat_id, created_at)
            VALUES (:name, :chat_id, :created_at)
            RETURNING id
            """
        ),
        {
            "name": fallback_name,
            "chat_id": str(telegram_chat_id),
            "created_at": now_utc().replace(tzinfo=None),
        },
    ).first()
    db.session.commit()
    return int(created[0])


def _get_latest_week_number() -> Optional[int]:
    row = db.session.execute(
        text(
            """
            SELECT week_number
            FROM weeks
            ORDER BY season_year DESC, week_number DESC
            LIMIT 1
            """
        )
    ).first()
    return int(row[0]) if row else None


def _get_week_id(week_number: int) -> Optional[int]:
    row = db.session.execute(
        text(
            """
            SELECT id
            FROM weeks
            WHERE week_number = :w
            ORDER BY season_year DESC
            LIMIT 1
            """
        ),
        {"w": int(week_number)},
    ).first()
    return int(row[0]) if row else None


def _load_week_games_with_user_pick(week_id: int, participant_id: int) -> List[Tuple]:
    """
    Returns list of tuples:
    (game_id, game_time, away_team, home_team, selected_team)
    selected_team may be None if not picked yet.
    """
    result = db.session.execute(
        text(
            """
            SELECT g.id AS game_id,
                   g.game_time,
                   g.away_team,
                   g.home_team,
                   p.selected_team
            FROM games g
            LEFT JOIN picks p
              ON p.game_id = g.id
             AND p.participant_id = :pid
            WHERE g.week_id = :week_id
            ORDER BY g.game_time NULLS LAST, g.id
            """
        ),
        {"pid": participant_id, "week_id": week_id},
    ).fetchall()
    return list(result)


# ---------- handlers ----------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start — register user if necessary and explain commands.
    """
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    participant_id = _get_or_create_participant(str(chat.id), user.first_name)

    msg = [
        f"Hi {user.first_name or 'there'}! You're set up (participant #{participant_id}).",
        "",
        "Commands:",
        "• /mypicks — show your picks for the latest week",
        "• /mypicks <week> — show your picks for a specific week number (e.g., /mypicks 4)",
    ]
    await update.message.reply_text("\n".join(msg))


async def mypicks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /mypicks [week_number]
    Shows the user's picks for the specified (or latest) week by reading existing tables:
    weeks, games, picks, participants.
    """
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    # ensure participant exists
    participant_id = _get_or_create_participant(str(chat.id), user.first_name)

    # which week?
    week_arg = context.args[0] if context.args else None
    if week_arg and week_arg.isdigit():
        week_number = int(week_arg)
    else:
        week_number = _get_latest_week_number()

    if week_number is None:
        await update.message.reply_text("No weeks found in the database yet.")
        return

    week_id = _get_week_id(week_number)
    if week_id is None:
        await update.message.reply_text(f"I couldn't find week {week_number} in the database.")
        return

    games = _load_week_games_with_user_pick(week_id, participant_id)
    if not games:
        await update.message.reply_text(f"No games found for week {week_number}.")
        return

    lines = [f"*Week {week_number} — Your Picks*"]
    for idx, (game_id, game_time, away, home, selected) in enumerate(games, start=1):
        when = game_time.strftime("%a %m/%d %H:%M") if game_time else "TBD"
        pick_str = selected if selected else "—"
        lines.append(f"{idx}. {away} @ {home} — *{pick_str}* ({when})")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# Optional: simple health handler used by the webhook or scheduler
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong")

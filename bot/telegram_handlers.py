# bot/telegram_handlers.py

import logging
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import text
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Import your SQLAlchemy session from models (Heroku logs showed this is correct)
from models import db

log = logging.getLogger(__name__)


# --- small helpers -----------------------------------------------------------


def now_utc() -> datetime:
    """Return an aware UTC datetime without relying on any project utils."""
    return datetime.now(timezone.utc)


def _display_name(first_name: Optional[str], username: Optional[str]) -> str:
    if first_name:
        return first_name
    if username:
        return f"@{username}"
    return "Friend"


# --- participant bootstrap ---------------------------------------------------


def _get_participant_by_chat_id(chat_id: int) -> Optional[int]:
    """Returns participant.id for this telegram_chat_id, or None."""
    row = db.session.execute(
        text(
            """
            select id
            from participants
            where telegram_chat_id = :chat_id
            limit 1
            """
        ),
        {"chat_id": str(chat_id)},  # column is likely text/varchar in your schema
    ).first()
    return row[0] if row else None


def _insert_participant(chat_id: int, name: str) -> int:
    """Create a participant and return its id."""
    result = db.session.execute(
        text(
            """
            insert into participants (name, telegram_chat_id, created_at)
            values (:name, :chat_id, :created_at)
            returning id
            """
        ),
        {
            "name": (name or "Friend")[:80],
            "chat_id": str(chat_id),
            # if column is TIMESTAMP WITHOUT TIME ZONE, store naive UTC:
            "created_at": now_utc().replace(tzinfo=None),
        },
    )
    pid = result.scalar_one()
    db.session.commit()
    return pid


def ensure_participant(chat_id: int, name_hint: str) -> int:
    """Fetch or create a participant row for this Telegram chat."""
    pid = _get_participant_by_chat_id(chat_id)
    if pid:
        return pid
    return _insert_participant(chat_id, name_hint)


# --- command handlers --------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registers the user (if new) and says hello."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    display = _display_name(user.first_name, user.username)
    pid = ensure_participant(chat.id, display)

    msg = (
        f"Hey {display}! 👋\n\n"
        "You’re set up for picks. Use:\n"
        "• /mypicks — see all the picks we have on record\n"
        "• /help — list commands\n"
    )
    log.info("start: chat_id=%s participant_id=%s", chat.id, pid)
    await context.bot.send_message(chat_id=chat.id, text=msg)


async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    await context.bot.send_message(
        chat_id=chat.id,
        text="Commands:\n"
        "• /start — register & info\n"
        "• /mypicks — list your picks\n"
        "• /ping — quick health check",
    )


async def mypicks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List this chat's picks across weeks using the real schema."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    display = _display_name(user.first_name, user.username)
    pid = ensure_participant(chat.id, display)

    # Pull picks joined to games & weeks
    rows: Sequence = db.session.execute(
        text(
            """
            select
                w.season_year,
                w.week_number,
                g.home_team,
                g.away_team,
                p.selected_team,
                g.status,
                g.home_score,
                g.away_score
            from picks p
            join games g on g.id = p.game_id
            join weeks w on w.id = g.week_id
            where p.participant_id = :pid
            order by w.week_number, g.id
            """
        ),
        {"pid": pid},
    ).fetchall()

    if not rows:
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"I don’t have any picks on file for you yet, {display}.\n"
                "Make a pick and try again with /mypicks."
            ),
        )
        return

    # Group by week and render
    lines: list[str] = []
    current_week = None
    for (
        season_year,
        week_number,
        home_team,
        away_team,
        selected_team,
        status,
        home_score,
        away_score,
    ) in rows:
        if week_number != current_week:
            current_week = week_number
            lines.append(f"\n<b>Week {week_number} ({season_year})</b>")
        maybe_score = ""
        if status and status.lower() in {"final", "in_progress", "post", "completed"}:
            if home_score is not None and away_score is not None:
                maybe_score = f"  —  {away_team} {away_score} @ {home_team} {home_score}"
        lines.append(
            f"• {away_team} at {home_team}  —  <b>you picked: {selected_team}</b>{maybe_score}"
        )

    header = f"{display}, here are your picks:"
    body = "\n".join(lines).lstrip()
    text_out = f"{header}\n{body}"

    await context.bot.send_message(
        chat_id=chat.id,
        text=text_out,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lightweight sanity check used by the worker."""
    chat = update.effective_chat
    if not chat:
        return
    await context.bot.send_message(chat_id=chat.id, text="pong")

# jobs.py
import os
import logging
from datetime import datetime
from flask import current_app
from models import db, Week, Game, Participant, Pick, Reminder
from telegram import Bot

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Telegram Bot setup
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in environment variables.")

bot = Bot(token=TELEGRAM_BOT_TOKEN)

# --- Helper: Send Telegram Message ---
def send_telegram_message(chat_id: str, text: str):
    try:
        bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        logger.info(f"✅ Sent Telegram message to {chat_id}")
    except Exception as e:
        logger.error(f"❌ Failed to send message to {chat_id}: {e}")


# --- Weekly Launch ---
def send_week_launch_telegram_message(week: Week):
    """
    Notify participants of the new week's games and request picks.
    """
    games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()
    if not games:
        logger.warning(f"No games found for Week {week.week_number}")
        return

    message_lines = [f"🏈 <b>NFL Week {week.week_number}, {week.season_year}</b>"]
    for g in games:
        message_lines.append(
            f"{g.away_team} @ {g.home_team} — {g.game_time.strftime('%a %m/%d %I:%M %p')}"
        )
    message_lines.append("\nReply with your picks before Thursday kickoff!")

    text = "\n".join(message_lines)

    participants = Participant.query.all()
    for p in participants:
        if p.telegram_chat_id:
            send_telegram_message(p.telegram_chat_id, text)
            reminder = Reminder(
                week_id=week.id,
                participant_id=p.id,
                kind="launch",
                channel="telegram",
                status="sent",
                sent_at=datetime.utcnow(),
            )
            db.session.add(reminder)

    db.session.commit()
    logger.info(f"✅ Launch message sent for Week {week.week_number}")


# --- Reminders ---
def check_and_send_reminders():
    """
    Send a reminder to participants who haven’t submitted picks.
    """
    week = Week.query.order_by(Week.week_number.desc()).first()
    if not week:
        logger.warning("No active week.")
        return

    picks = Pick.query.filter(Pick.game_id.in_([g.id for g in week.games])).all()
    picked_participants = {p.participant_id for p in picks}
    participants = Participant.query.all()

    for p in participants:
        if p.id not in picked_participants and p.telegram_chat_id:
            send_telegram_message(
                p.telegram_chat_id,
                f"⏰ Reminder: Submit your picks for Week {week.week_number} before the deadline!",
            )
            reminder = Reminder(
                week_id=week.id,
                participant_id=p.id,
                kind="reminder",
                channel="telegram",
                status="sent",
                sent_at=datetime.utcnow(),
            )
            db.session.add(reminder)

    db.session.commit()


# --- Weekly Results ---
def calculate_and_send_results():
    """
    Send win/loss results for the most recent week.
    """
    week = Week.query.order_by(Week.week_number.desc()).first()
    if not week:
        logger.warning("No active week.")
        return

    participants = Participant.query.all()
    message_lines = [f"📊 <b>Results — Week {week.week_number}</b>"]

    for p in participants:
        wins = (
            db.session.query(db.func.count(Pick.id))
            .join(Game)
            .filter(
                Pick.participant_id == p.id,
                Game.week_id == week.id,
                Game.winner.isnot(None),
                Pick.selected_team == Game.winner,
            )
            .scalar()
            or 0
        )
        losses = (
            db.session.query(db.func.count(Pick.id))
            .join(Game)
            .filter(
                Pick.participant_id == p.id,
                Game.week_id == week.id,
                Game.winner.isnot(None),
                Pick.selected_team != Game.winner,
            )
            .scalar()
            or 0
        )
        message_lines.append(f"{p.name}: {wins}W - {losses}L")

    text = "\n".join(message_lines)

    for p in participants:
        if p.telegram_chat_id:
            send_telegram_message(p.telegram_chat_id, text)

    logger.info(f"✅ Results sent for Week {week.week_number}")


# --- Season Results ---
def calculate_and_send_season_results():
    """
    Send overall results for the season (all weeks combined).
    """
    season = datetime.utcnow().year
    participants = Participant.query.all()
    message_lines = [f"🏆 <b>Season Results {season}</b>"]

    for p in participants:
        wins = (
            db.session.query(db.func.count(Pick.id))
            .join(Game)
            .join(Week)
            .filter(
                Pick.participant_id == p.id,
                Week.season_year == season,
                Game.winner.isnot(None),
                Pick.selected_team == Game.winner,
            )
            .scalar()
            or 0
        )
        losses = (
            db.session.query(db.func.count(Pick.id))
            .join(Game)
            .join(Week)
            .filter(
                Pick.participant_id == p.id,
                Week.season_year == season,
                Game.winner.isnot(None),
                Pick.selected_team != Game.winner,
            )
            .scalar()
            or 0
        )
        message_lines.append(f"{p.name}: {wins}W - {losses}L")

    text = "\n".join(message_lines)

    for p in participants:
        if p.telegram_chat_id:
            send_telegram_message(p.telegram_chat_id, text)

    logger.info("✅ Season results sent")


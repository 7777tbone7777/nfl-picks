# jobs.py
import os
import logging
import asyncio
from datetime import datetime
from flask import current_app
from models import db, Week, Game, Participant, Pick, Reminder
from telegram import Bot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# Telegram Messaging
# =========================
def send_telegram_message(participant, text):
    """
    Send a Telegram message to the participant using python-telegram-bot v20+.
    participant.telegram_chat_id must be the numeric chat_id, not @username.
    """
    if not participant.telegram_chat_id:
        logger.warning(f"⚠️ No telegram_chat_id for {participant.name}")
        return

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in environment")

    bot = Bot(token=token)

    async def _send():
        try:
            await bot.send_message(
                chat_id=participant.telegram_chat_id,
                text=text,
                parse_mode="HTML",
            )
            logger.info(f"✅ Sent Telegram message to {participant.name}")
        except Exception as e:
            logger.error(f"❌ Failed to send Telegram message to {participant.name}: {e}")

    asyncio.run(_send())

# =========================
# Weekly Jobs
# =========================
def send_week_launch_sms(week: Week):  # kept name for compatibility
    """
    Notify all participants when a new week is ready.
    """
    participants = Participant.query.all()
    for p in participants:
        msg = (
            f"🏈 NFL Picks Week {week.week_number} ({week.season_year})\n\n"
            f"Deadline: {week.picks_deadline}\n"
            f"Submit your picks before kickoff!"
        )
        send_telegram_message(p, msg)

    r = Reminder(
        week_id=week.id,
        kind="launch",
        channel="telegram",
        status="sent",
        sent_at=datetime.utcnow(),
    )
    db.session.add(r)
    db.session.commit()
    logger.info(f"✅ Launch reminder logged for Week {week.week_number}")

def calculate_and_send_results():
    """
    Calculate results for the latest completed week and send messages.
    """
    latest_week = Week.query.order_by(Week.week_number.desc()).first()
    if not latest_week:
        logger.warning("⚠️ No week found for results")
        return

    results = []
    for p in Participant.query.all():
        wins = (
            db.session.query(db.func.count(Pick.id))
            .join(Game)
            .filter(
                Pick.participant_id == p.id,
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
                Game.winner.isnot(None),
                Pick.selected_team != Game.winner,
            )
            .scalar()
            or 0
        )
        results.append((p.name, wins, losses))

    # sort by wins
    results.sort(key=lambda r: r[1], reverse=True)

    # build message
    msg = f"📊 Results for Week {latest_week.week_number}, {latest_week.season_year}\n\n"
    for name, w, l in results:
        msg += f"{name}: {w}W - {l}L\n"

    # send to all
    for p in Participant.query.all():
        send_telegram_message(p, msg)

    r = Reminder(
        week_id=latest_week.id,
        kind="results",
        channel="telegram",
        status="sent",
        sent_at=datetime.utcnow(),
    )
    db.session.add(r)
    db.session.commit()
    logger.info("✅ Results reminder logged")

def calculate_and_send_season_results():
    """
    End-of-season standings (before playoffs).
    """
    latest_week = Week.query.order_by(Week.week_number.desc()).first()
    if not latest_week:
        logger.warning("⚠️ No week found for season results")
        return

    results = []
    for p in Participant.query.all():
        wins = (
            db.session.query(db.func.count(Pick.id))
            .join(Game)
            .filter(
                Pick.participant_id == p.id,
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
                Game.winner.isnot(None),
                Pick.selected_team != Game.winner,
            )
            .scalar()
            or 0
        )
        results.append((p.name, wins, losses))

    results.sort(key=lambda r: r[1], reverse=True)
    winner = results[0]

    msg = f"🏆 Season Results {latest_week.season_year}\n\n"
    for name, w, l in results:
        msg += f"{name}: {w}W - {l}L\n"
    msg += f"\n🥇 Winner: {winner[0]} with {winner[1]} wins!"

    for p in Participant.query.all():
        send_telegram_message(p, msg)

    logger.info("✅ Season results sent")


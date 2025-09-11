import os
import logging
import asyncio
from telegram import Bot

from flask_app import create_app
from models import db, Participant, Week, Game, Pick

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# -------------------------------
# Telegram Messaging
# -------------------------------
def send_telegram_message(participant, text: str):
    """
    Safely send a Telegram message to a participant.
    Wraps the async bot.send_message in asyncio.run so we don't get 'never awaited'.
    """
    chat_id = participant.telegram_chat_id
    if not chat_id:
        logger.warning(f"⚠️ No telegram_chat_id for {participant.name}")
        return False

    async def _send():
        return await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")

    try:
        asyncio.run(_send())
        logger.info(f"✅ Sent Telegram message to {participant.name}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send Telegram message to {participant.name}: {e}")
        return False

# -------------------------------
# Weekly SMS/Telegram Job
# -------------------------------
def send_week_launch_sms(week: Week):
    participants = Participant.query.all()
    for p in participants:
        send_telegram_message(p, f"🏈 Picks are open for Week {week.week_number}, {week.season_year}!")

# -------------------------------
# Results Job
# -------------------------------
def calculate_and_send_results():
    weeks = Week.query.all()
    for wk in weeks:
        results_summary = f"📊 Results for Week {wk.week_number}, {wk.season_year}\n"
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
            results_summary += f"\n{p.name}: {wins}W - {losses}L"
        for p in Participant.query.all():
            send_telegram_message(p, results_summary)

# -------------------------------
# Manual test helper
# -------------------------------
if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        p = Participant.query.filter_by(name="Tony").first()
        send_telegram_message(p, "🚀 Test message after fixing async issue!")


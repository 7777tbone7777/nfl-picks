import os
import logging
import httpx
from flask_app import create_app
from models import db, Participant, Week, Game

# --- Logging setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Telegram setup ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# --- Send a message to a participant ---
def send_telegram_message(participant: Participant, text: str) -> bool:
    """Send a Telegram message to a participant if they have a chat_id."""
    if not participant.telegram_chat_id:
        logger.warning(f"⚠️ No telegram_chat_id for {participant.name}")
        return False

    chat_id = participant.telegram_chat_id
    try:
        resp = httpx.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(f"✅ Sent Telegram message to {participant.name}")
            return True
        else:
            logger.error(f"❌ Failed to send Telegram message to {participant.name}: {resp.text}")
            return False
    except Exception as e:
        logger.exception(f"💥 Error sending message to {participant.name}: {e}")
        return False


# --- Send weekly games list ---
def send_week_games(week_number: int, season_year: int = 2025):
    """Send out the list of games for a given week to all participants."""
    app = create_app()
    with app.app_context():
        week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()
        if not week:
            logger.error(f"❌ No week found for {season_year} W{week_number}")
            return

        games = Game.query.filter_by(week_id=week.id).order_by(Game.start_time).all()
        if not games:
            logger.error(f"❌ No games found for {season_year} W{week_number}")
            return

        text = f"📅 NFL Picks – Week {week_number}, {season_year}\n\n"
        for idx, g in enumerate(games, start=1):
            text += f"{idx}. {g.away_team} @ {g.home_team} – {g.start_time.strftime('%a %b %d %I:%M %p')}\n"

        participants = Participant.query.all()
        for p in participants:
            if p.telegram_chat_id:
                send_telegram_message(p, text)


# --- Placeholder: Future functions ---
def send_week_launch_sms(week: Week):
    """Stub for SMS sending (if needed later)."""
    logger.info(f"📱 Would send SMS launch message for Week {week.week_number}")


def calculate_and_send_results():
    """Stub for weekly results calculation."""
    logger.info("📊 Would calculate and send results here.")


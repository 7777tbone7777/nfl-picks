import os
import sys
import logging
import httpx
import argparse
from flask_app import create_app
from models import db, Participant, Week, Game, Pick

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

    try:
        resp = httpx.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": participant.telegram_chat_id, "text": text, "parse_mode": "HTML"},
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
    """Send out the list of games for a given week to all participants with inline pick buttons."""
    app = create_app()
    with app.app_context():
        week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()
        if not week:
            logger.error(f"❌ No week found for {season_year} W{week_number}")
            return

        games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()
        if not games:
            logger.error(f"❌ No games found for {season_year} W{week_number}")
            return

        for g in games:
            local_time = g.game_time.replace(
                tzinfo=__import__("zoneinfo").ZoneInfo("UTC")
            ).astimezone(__import__("zoneinfo").ZoneInfo("America/Los_Angeles"))

            text = f"{g.away_team} @ {g.home_team}\n{local_time.strftime('%a %b %d %I:%M %p PT')}"

            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": g.away_team, "callback_data": f"pick:{g.id}:{g.away_team}"},
                        {"text": g.home_team, "callback_data": f"pick:{g.id}:{g.home_team}"},
                    ]
                ]
            }

            participants = Participant.query.all()
            for p in participants:
                if not p.telegram_chat_id:
                    continue
                try:
                    resp = httpx.post(
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "chat_id": p.telegram_chat_id,
                            "text": text,
                            "reply_markup": keyboard,
                        },
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        logger.info(f"✅ Sent game to {p.name}: {text}")
                    else:
                        logger.error(f"❌ Telegram error for {p.name}: {resp.text}")
                except Exception as e:
                    logger.exception(f"💥 Error sending game to {p.name}: {e}")


# --- Placeholder: Future functions ---
def send_week_launch_sms(week: Week):
    """Stub for SMS sending (if needed later)."""
    logger.info(f"📱 Would send SMS launch message for Week {week.week_number}")


def calculate_and_send_results():
    """Stub for weekly results calculation."""
    logger.info("📊 Would calculate and send results here.")


# --- CLI entrypoint ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run jobs for NFL Picks app")
    subparsers = parser.add_subparsers(dest="command")

    # Send weekly games
    send_games_parser = subparsers.add_parser("send_week_games", help="Send out weekly games")
    send_games_parser.add_argument("week_number", type=int, help="Week number")
    send_games_parser.add_argument("--season_year", type=int, default=2025, help="Season year (default: 2025)")

    args = parser.parse_args()

    if args.command == "send_week_games":
        send_week_games(args.week_number, args.season_year)
    else:
        parser.print_help()
        sys.exit(1)


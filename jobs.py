import os
import sys
import logging
import httpx
import argparse
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

        games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()
        if not games:
            logger.error(f"❌ No games found for {season_year} W{week_number}")
            return

        participants = Participant.query.all()
        for p in participants:
            if not p.telegram_chat_id:
                continue

            for g in games:
                local_time = g.game_time.replace(
                    tzinfo=__import__("zoneinfo").ZoneInfo("UTC")
                ).astimezone(__import__("zoneinfo").ZoneInfo("America/Los_Angeles"))

                text = f"{g.away_team} @ {g.home_team}\n{local_time.strftime('%a %b %d %I:%M %p PT')}"

                # ✅ FIX: use raw JSON for inline keyboard
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": g.away_team, "callback_data": f"pick:{g.id}:{g.away_team}"},
                            {"text": g.home_team, "callback_data": f"pick:{g.id}:{g.home_team}"}
                        ]
                    ]
                }

                try:
                    resp = httpx.post(
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "chat_id": p.telegram_chat_id,
                            "text": text,
                            "reply_markup": reply_markup,
                            "parse_mode": "HTML",
                        },
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        logger.info(f"✅ Sent game to {p.name}: {g.away_team} @ {g.home_team}")
                    else:
                        logger.error(f"❌ Failed to send game to {p.name}: {resp.text}")
                except Exception as e:
                    logger.exception(f"💥 Error sending game to {p.name}: {e}")


# --- Placeholder: Future functions ---
def send_week_launch_sms(week: Week):
    """Stub for SMS sending (if needed later)."""
    logger.info(f"📱 Would send SMS launch message for Week {week.week_number}")


def calculate_and_send_results():
    """Stub for weekly results calculation."""
    logger.info("📊 Would calculate and send results here.")


def run_telegram_listener():
    """Start polling Telegram for button presses (/ callback queries)."""
    from telegram.ext import Application, CallbackQueryHandler

    async def handle_pick(update, context):
        query = update.callback_query
        await query.answer()

        data = query.data  # looks like "pick:123:TeamName"
        _, game_id, team = data.split(":")

        await query.edit_message_text(f"✅ You picked {team}")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CallbackQueryHandler(handle_pick))
    application.run_polling()


def reset_picks_for_participant(participant_name: str):
    """Delete all picks for a participant by name."""
    app = create_app()
    with app.app_context():
        p = Participant.query.filter_by(name=participant_name).first()
        if not p:
            logger.error(f"❌ Participant not found: {participant_name}")
            return
        deleted = db.session.query("picks").filter_by(participant_id=p.id).delete()
        db.session.commit()
        logger.info(f"✅ Picks reset for {participant_name} ({deleted} deleted)")


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


import os
import sys
import logging
import httpx
import argparse
from flask_app import create_app
from models import db, Participant, Week, Game, Pick

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
)

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


# --- Send weekly games list with inline buttons ---
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
                keyboard = [
                    [
                        InlineKeyboardButton(g.home_team, callback_data=f"{p.id}:{g.id}:{g.home_team}"),
                        InlineKeyboardButton(g.away_team, callback_data=f"{p.id}:{g.id}:{g.away_team}"),
                    ]
                ]

                try:
                    resp = httpx.post(
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "chat_id": p.telegram_chat_id,
                            "text": text,
                            "reply_markup": {"inline_keyboard": keyboard},
                        },
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        logger.info(f"✅ Sent game to {p.name}: {g.away_team} @ {g.home_team}")
                    else:
                        logger.error(f"❌ Failed to send game to {p.name}: {resp.text}")
                except Exception as e:
                    logger.exception(f"💥 Error sending game to {p.name}: {e}")


# --- Handle button presses ---
async def handle_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses and save pick to DB."""
    query = update.callback_query
    await query.answer()

    try:
        participant_id, game_id, team = query.data.split(":")
        participant_id, game_id = int(participant_id), int(game_id)

        app = create_app()
        with app.app_context():
            existing_pick = Pick.query.filter_by(
                participant_id=participant_id, game_id=game_id
            ).first()

            if existing_pick:
                existing_pick.selected_team = team
            else:
                new_pick = Pick(
                    participant_id=participant_id, game_id=game_id, selected_team=team
                )
                db.session.add(new_pick)

            db.session.commit()

        await query.edit_message_text(f"✅ Pick saved: {team}")
        logger.info(f"✅ Pick saved for participant {participant_id}, game {game_id}: {team}")

    except Exception as e:
        logger.exception(f"💥 Error handling pick: {e}")
        await query.edit_message_text("❌ Error saving your pick. Please try again.")


# --- Run Telegram listener (polling mode) ---
def run_telegram_listener():
    """Run the Telegram bot to listen for button presses."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not set in environment.")
        sys.exit(1)

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CallbackQueryHandler(handle_pick))

    logger.info("🤖 Telegram bot listener started. Waiting for button presses...")
    application.run_polling()


# --- CLI entrypoint ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run jobs for NFL Picks app")
    subparsers = parser.add_subparsers(dest="command")

    send_games_parser = subparsers.add_parser("send_week_games", help="Send out weekly games")
    send_games_parser.add_argument("week_number", type=int, help="Week number")
    send_games_parser.add_argument("--season_year", type=int, default=2025, help="Season year (default: 2025)")

    listen_parser = subparsers.add_parser("listen", help="Run Telegram listener for picks")

    args = parser.parse_args()

    if args.command == "send_week_games":
        send_week_games(args.week_number, args.season_year)
    elif args.command == "listen":
        run_telegram_listener()
    else:
        parser.print_help()
        sys.exit(1)


import os
import sys
import logging
import httpx
import argparse
from flask_app import create_app
from models import db, Participant, Week, Game, Pick
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

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
async def send_week_games(update: Update, context: ContextTypes.DEFAULT_TYPE, week_number: int, season_year: int = 2025):
    """Send out the list of games for a given week to the requesting user."""
    app = create_app()
    with app.app_context():
        week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()
        if not week:
            await update.message.reply_text(f"❌ No week found for {season_year} W{week_number}")
            return

        games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()
        if not games:
            await update.message.reply_text(f"❌ No games found for {season_year} W{week_number}")
            return

        for g in games:
            keyboard = [
                [
                    InlineKeyboardButton(
                        g.away_team, callback_data=f"pick_{g.id}_{g.away_team}"
                    ),
                    InlineKeyboardButton(
                        g.home_team, callback_data=f"pick_{g.id}_{g.home_team}"
                    ),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = f"{g.away_team} @ {g.home_team} – {g.game_time.strftime('%a %b %d %I:%M %p')}"
            await update.message.reply_text(text, reply_markup=reply_markup)


# --- Handle pick button presses ---
async def handle_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when a participant picks a team."""
    query = update.callback_query
    await query.answer()

    data = query.data
    try:
        _, game_id, team = data.split("_", 2)  # safe split for team names with spaces
    except ValueError:
        logger.error(f"⚠️ Bad callback data: {data}")
        await query.edit_message_text("⚠️ Something went wrong saving your pick.")
        return

    user_id = str(query.from_user.id)

    app = create_app()
    with app.app_context():
        participant = Participant.query.filter_by(telegram_chat_id=user_id).first()
        if not participant:
            await query.edit_message_text("⚠️ You’re not registered in the system.")
            return

        pick = Pick.query.filter_by(participant_id=participant.id, game_id=game_id).first()
        if pick:
            pick.team = team
        else:
            pick = Pick(participant_id=participant.id, game_id=game_id, team=team)
            db.session.add(pick)
        db.session.commit()

        await query.edit_message_text(f"✅ You picked {team}")


# --- Telegram bot listener ---
def run_telegram_listener():
    """Start the Telegram bot listener for commands and picks."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not set in environment.")
        sys.exit(1)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("sendweek", sendweek_command))
    app.add_handler(CallbackQueryHandler(handle_pick))

    logger.info("🤖 Telegram bot listener started...")
    app.run_polling()


# --- Command handler wrapper for /sendweek ---
async def sendweek_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /sendweek <week_number> [season_year]")
        return
    week_number = int(context.args[0])
    season_year = int(context.args[1]) if len(context.args) > 1 else 2025
    await send_week_games(update, context, week_number, season_year)


# --- CLI entrypoint ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run jobs for NFL Picks app")
    subparsers = parser.add_subparsers(dest="command")

    # Run Telegram listener
    subparsers.add_parser("listen", help="Run Telegram listener for picks")

    args = parser.parse_args()

    if args.command == "listen":
        run_telegram_listener()
    else:
        parser.print_help()
        sys.exit(1)


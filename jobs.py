import os
import sys
import logging
import httpx
import argparse
from flask_app import create_app
from models import db, Participant, Week, Game, Pick
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
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


# --- Telegram handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome to NFL Picks! Use /sendweek <week> <year> to get started.")


async def handle_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # ✅ Always answer to avoid button freeze

    data = query.data  # e.g., "pick_123_home"
    try:
        _, game_id, team = data.split("_")
        game_id = int(game_id)

        with create_app().app_context():
            game = Game.query.get(game_id)
            participant = Participant.query.filter_by(telegram_chat_id=str(query.from_user.id)).first()

            if not game or not participant:
                await query.edit_message_text("❌ Could not record your pick. Try again.")
                return

            # Save or update pick
            pick = Pick.query.filter_by(participant_id=participant.id, game_id=game.id).first()
            if not pick:
                pick = Pick(participant_id=participant.id, game_id=game.id, selected_team=team)
                db.session.add(pick)
            else:
                pick.selected_team = team
            db.session.commit()

            await query.edit_message_text(f"✅ You picked {team} in {game.away_team} @ {game.home_team}")
            logger.info(f"{participant.name} picked {team} for game {game.id}")

    except Exception as e:
        logger.exception(f"💥 Error handling pick: {e}")
        await query.edit_message_text("⚠️ Something went wrong saving your pick.")


async def send_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send out games for the requested week."""
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /sendweek <week_number> [season_year]")
        return

    week_number = int(context.args[0])
    season_year = int(context.args[1]) if len(context.args) > 1 else 2025

    with create_app().app_context():
        week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()
        if not week:
            await update.message.reply_text(f"❌ No week {week_number} found for {season_year}")
            return

        games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()
        if not games:
            await update.message.reply_text(f"❌ No games scheduled for week {week_number}")
            return

        for g in games:
            keyboard = [
                [
                    InlineKeyboardButton(g.away_team, callback_data=f"pick_{g.id}_{g.away_team}"),
                    InlineKeyboardButton(g.home_team, callback_data=f"pick_{g.id}_{g.home_team}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = f"{g.away_team} @ {g.home_team} – {g.game_time.strftime('%a %b %d %I:%M %p')}"
            await update.message.reply_text(text, reply_markup=reply_markup)


# --- Run Telegram listener ---
def run_telegram_listener():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("❌ TELEGRAM_BOT_TOKEN is not set in environment variables.")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sendweek", send_week))
    app.add_handler(CallbackQueryHandler(handle_pick))

    logger.info("🤖 Telegram bot listener started...")
    app.run_polling()


# --- CLI entrypoint ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run jobs for NFL Picks app")
    subparsers = parser.add_subparsers(dest="command")

    # Run Telegram bot
    subparsers.add_parser("runbot", help="Run the Telegram bot listener")

    args = parser.parse_args()

    if args.command == "runbot":
        run_telegram_listener()
    else:
        parser.print_help()
        sys.exit(1)


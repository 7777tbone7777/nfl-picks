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


# --- Send a message to a participant (plain text) ---
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
            logger.error(
                f"❌ Failed to send Telegram message to {participant.name}: {resp.text}"
            )
            return False
    except Exception as e:
        logger.exception(f"💥 Error sending message to {participant.name}: {e}")
        return False


# --- Command: /testgame ---
async def testgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a single test game with buttons."""
    app = create_app()
    with app.app_context():
        g = Game.query.first()
        if not g:
            await update.message.reply_text("❌ No games in DB yet.")
            return

        keyboard = [
            [
                InlineKeyboardButton(g.away_team, callback_data=f"{g.id}:{g.away_team}"),
                InlineKeyboardButton(g.home_team, callback_data=f"{g.id}:{g.home_team}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = f"🏈 {g.away_team} @ {g.home_team}\n📅 {g.game_time.strftime('%a %b %d %I:%M %p')}"
        await update.message.reply_text(text, reply_markup=reply_markup)


# --- Callback for button presses ---
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses for picks."""
    query = update.callback_query
    await query.answer()

    game_id, team = query.data.split(":")

    app = create_app()
    with app.app_context():
        participant = Participant.query.filter_by(
            telegram_chat_id=str(query.from_user.id)
        ).first()
        if not participant:
            await query.edit_message_text("❌ You’re not registered as a participant.")
            return

        # Save or update pick
        pick = Pick.query.filter_by(
            participant_id=participant.id, game_id=game_id
        ).first()
        if not pick:
            pick = Pick(
                participant_id=participant.id, game_id=game_id, selected_team=team
            )
            db.session.add(pick)
        else:
            pick.selected_team = team
        db.session.commit()

        await query.edit_message_text(f"✅ You picked {team}")


# --- Command: /sendweek ---
async def sendweek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send all games for a given week with buttons."""
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /sendweek <week_number> [season_year]")
        return

    week_number = int(context.args[0])
    season_year = int(context.args[1]) if len(context.args) > 1 else 2025

    app = create_app()
    with app.app_context():
        week = Week.query.filter_by(
            week_number=week_number, season_year=season_year
        ).first()
        if not week:
            await update.message.reply_text(f"❌ No data for Week {week_number}, {season_year}")
            return

        games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()
        if not games:
            await update.message.reply_text(f"❌ No games found for Week {week_number}, {season_year}")
            return

        for g in games:
            keyboard = [
                [
                    InlineKeyboardButton(g.away_team, callback_data=f"{g.id}:{g.away_team}"),
                    InlineKeyboardButton(g.home_team, callback_data=f"{g.id}:{g.home_team}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            text = f"🏈 {g.away_team} @ {g.home_team}\n📅 {g.game_time.strftime('%a %b %d %I:%M %p')}"
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=reply_markup,
            )


# --- Run Telegram listener ---
def run_telegram_listener():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("testgame", testgame))
    application.add_handler(CommandHandler("sendweek", sendweek))
    application.add_handler(CallbackQueryHandler(button))
    logger.info("🚀 Telegram listener running...")
    application.run_polling()


# --- CLI entrypoint ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run jobs for NFL Picks app")
    subparsers = parser.add_subparsers(dest="command")

    # Send weekly games (manual CLI trigger, not via bot)
    send_games_parser = subparsers.add_parser(
        "send_week_games", help="Send out weekly games"
    )
    send_games_parser.add_argument("week_number", type=int, help="Week number")
    send_games_parser.add_argument(
        "--season_year", type=int, default=2025, help="Season year (default: 2025)"
    )

    args = parser.parse_args()

    if args.command == "send_week_games":
        sendweek(args.week_number, args.season_year)
    else:
        parser.print_help()
        sys.exit(1)


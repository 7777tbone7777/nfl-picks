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


# --- Inline keyboard for each game ---
def build_game_keyboard(game: Game):
    """Return inline keyboard with two buttons (away/home)."""
    keyboard = [
        [
            InlineKeyboardButton(
                game.away_team, callback_data=f"{game.id}:{game.away_team}"
            ),
            InlineKeyboardButton(
                game.home_team, callback_data=f"{game.id}:{game.home_team}"
            ),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# --- Handle a pick from inline buttons ---
async def handle_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        data = query.data  # format: "game_id:team"
        logger.info(f"🎯 Received callback data: {data}")
        game_id, team = data.split(":")

        tg_id = str(query.from_user.id)
        participant = Participant.query.filter_by(telegram_chat_id=tg_id).first()

        if not participant:
            await query.edit_message_text("⚠️ You are not registered.")
            return

        pick = Pick.query.filter_by(participant_id=participant.id, game_id=game_id).first()
        if not pick:
            pick = Pick(participant_id=participant.id, game_id=game_id, selected_team=team)
            db.session.add(pick)
        else:
            pick.pick = team

        db.session.commit()
        await query.edit_message_text(f"✅ You picked {team}")

    except Exception as e:
        logger.exception("💥 Error handling pick")
        await query.edit_message_text("⚠️ Something went wrong saving your pick.")


# --- Send weekly games list ---
def send_week_games(week_number: int, season_year: int = 2025):
    """Send out the list of games for a given week to all participants with inline buttons."""
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
                text = f"{g.away_team} @ {g.home_team}\n{g.game_time.strftime('%a %b %d %I:%M %p')}"
                try:
                    resp = httpx.post(
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "chat_id": p.telegram_chat_id,
                            "text": text,
                            "reply_markup": build_game_keyboard(g).to_dict(),
                        },
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        logger.info(f"📨 Sent game {g.id} to {p.name}")
                    else:
                        logger.error(f"❌ Failed to send game {g.id} to {p.name}: {resp.text}")
                except Exception as e:
                    logger.exception(f"💥 Error sending game {g.id} to {p.name}: {e}")


# --- Telegram bot listener ---
def run_telegram_listener():
    """Run the Telegram bot listener (handles /sendweek and button clicks)."""
    app = create_app()
    with app.app_context():
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        # Command: /sendweek <week> <year>
        async def sendweek_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                week_number = int(context.args[0])
                season_year = int(context.args[1]) if len(context.args) > 1 else 2025
                send_week_games(week_number, season_year)
                await update.message.reply_text(f"✅ Week {week_number} games sent.")
            except Exception as e:
                logger.exception("💥 Error in /sendweek command")
                await update.message.reply_text("⚠️ Usage: /sendweek <week_number> [season_year]")

        application.add_handler(CommandHandler("sendweek", sendweek_cmd))
        application.add_handler(CallbackQueryHandler(handle_pick))

        logger.info("🤖 Telegram bot listener started...")
        application.run_polling()


# --- CLI entrypoint ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run jobs for NFL Picks app")
    subparsers = parser.add_subparsers(dest="command")

    # Send weekly games
    send_games_parser = subparsers.add_parser("send_week_games", help="Send out weekly games")
    send_games_parser.add_argument("week_number", type=int, help="Week number")
    send_games_parser.add_argument("--season_year", type=int, default=2025, help="Season year (default: 2025)")

    # Run Telegram listener
    subparsers.add_parser("listen", help="Run Telegram listener")

    args = parser.parse_args()

    if args.command == "send_week_games":
        send_week_games(args.week_number, args.season_year)
    elif args.command == "listen":
        run_telegram_listener()
    else:
        parser.print_help()
        sys.exit(1)


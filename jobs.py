import os
import logging
from flask_app import create_app
from models import db, Participant, Game, Pick
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- Command: /testgame ---
async def testgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send one sample game with buttons."""
    keyboard = [
        [
            InlineKeyboardButton("Miami Dolphins", callback_data="1:Dolphins"),
            InlineKeyboardButton("Buffalo Bills", callback_data="1:Bills"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🏈 Test Game: Miami Dolphins @ Buffalo Bills\nPick your winner:",
        reply_markup=reply_markup,
    )

# --- Handle button presses ---
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    game_id, team = query.data.split(":")
    tg_id = str(query.from_user.id)

    app = create_app()
    with app.app_context():
        participant = Participant.query.filter_by(telegram_chat_id=tg_id).first()
        if participant:
            pick = Pick.query.filter_by(participant_id=participant.id, game_id=game_id).first()
            if not pick:
                pick = Pick(participant_id=participant.id, game_id=game_id, selected_team=team)
                db.session.add(pick)
            else:
                pick.selected_team = team
            db.session.commit()

            await query.edit_message_text(text=f"✅ You picked {team}")
            logger.info(f"{participant.name} picked {team} for game {game_id}")
        else:
            await query.edit_message_text(text="⚠️ You are not registered in the system.")

# --- Listener entrypoint ---
def run_telegram_listener():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("testgame", testgame))
    application.add_handler(CallbackQueryHandler(button))
    logger.info("🚀 Telegram listener running...")
    application.run_polling()


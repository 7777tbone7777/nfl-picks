from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import os

# Reuse your existing bot token
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Explicit mapping: Telegram usernames → Participant names
USERNAME_TO_PARTICIPANT = {
    "Tvzqz": "Tony",     # your Telegram username
    "KevHandle": "Kevin", # replace with Kevin's actual Telegram username
    "WillHandle": "Will", # replace with Will's actual Telegram username
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles /start messages: links Telegram chat_id to the correct Participant
    using USERNAME_TO_PARTICIPANT mapping.
    """
    from flask_app import create_app
    from models import db, Participant

    chat_id = update.message.chat.id
    username = update.message.from_user.username

    if not username:
        await update.message.reply_text(
            "⚠️ No username found in your Telegram account. Please set a username in Telegram settings."
        )
        return

    mapped_name = USERNAME_TO_PARTICIPANT.get(username)
    if not mapped_name:
        await update.message.reply_text(
            f"⚠️ Sorry, I don’t recognize your username @{username}. Please ask admin to add you."
        )
        return

    app = create_app()
    with app.app_context():
        p = Participant.query.filter_by(name=mapped_name).first()
        if p:
            p.telegram_chat_id = str(chat_id)
            db.session.commit()
            await update.message.reply_text(
                f"✅ Hi {p.name}, your Telegram account (@{username}) is now linked!"
            )
        else:
            await update.message.reply_text(
                f"⚠️ Couldn’t find a Participant record for {mapped_name}."
            )

def run_telegram_listener():
    """Run a lightweight bot listener (for linking chat IDs)."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in environment variables.")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    print("🚀 Telegram listener running... Send /start from Telegram to link accounts.")
    app.run_polling()


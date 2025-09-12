import os
import logging
import httpx
from flask_app import create_app
from models import db, Week, Game, Participant, Pick
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jobs")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_BOT_TOKEN')}"

def send_week_games(week_number, season_year):
    app = create_app()
    with app.app_context():
	week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()

        if not week:
            logger.error(f"❌ No week found for {season_year} week {week_number}")
            return

        participants = Participant.query.all()
        games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()

        for p in participants:
            if not p.telegram_chat_id:
                continue

            for g in games:
                local_time = g.game_time.replace(
                    tzinfo=__import__("zoneinfo").ZoneInfo("UTC")
                ).astimezone(__import__("zoneinfo").ZoneInfo("America/Los_Angeles"))
                text = f"{g.away_team} @ {g.home_team}\n{local_time.strftime('%a %b %d %I:%M %p PT')}"

                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(g.away_team, callback_data=f"pick:{p.id}:{g.id}:{g.away_team}"),
                        InlineKeyboardButton(g.home_team, callback_data=f"pick:{p.id}:{g.id}:{g.home_team}"),
                    ]
                ])

                try:
                    resp = httpx.post(
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "chat_id": p.telegram_chat_id,
                            "text": text,
                            "reply_markup": keyboard.to_dict()
                        }
                    )
                    resp.raise_for_status()
                    logger.info(f"✅ Sent game to {p.name}: {g.away_team} @ {g.home_team}")
                except Exception as e:
                    logger.error(f"💥 Error sending game to {p.name}: {e}")

async def handle_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, participant_id, game_id, team = query.data.split(":")
    except ValueError:
        return

    app = create_app()
    with app.app_context():
        pick = Pick.query.filter_by(participant_id=participant_id, game_id=game_id).first()
        if not pick:
            pick = Pick(participant_id=participant_id, game_id=game_id, team=team)
            db.session.add(pick)
        else:
            pick.team = team
        db.session.commit()

    await query.edit_message_text(f"✅ You picked {team}")

# ✅ NEW START HANDLER
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info(f"📩 /start received from {user.username} (id={chat_id})")

    # Optionally: store chat_id if this user exists in DB
    app = create_app()
    with app.app_context():
        participant = Participant.query.filter_by(name=user.username).first()
        if participant:
            participant.telegram_chat_id = str(chat_id)
            db.session.commit()
            logger.info(f"🔗 Linked {user.username} to chat_id {chat_id}")

    await update.message.reply_text("👋 Welcome! You are now registered to receive NFL picks.")

def run_telegram_listener():
    application = Application.builder().token(os.environ.get("TELEGRAM_BOT_TOKEN")).build()

    application.add_handler(CommandHandler("start", start))  # 👈 new handler
    application.add_handler(CallbackQueryHandler(handle_pick))

    application.run_polling()


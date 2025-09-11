import logging
import httpx
from flask_app import create_app
from models import db, Week, Game, Participant, Pick
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

def send_week_games(week_number, season_year):
    app = create_app()
    with app.app_context():
        week = Week.query.filter_by(week_id=week_number, season_year=season_year).first()
        if not week:
            logger.error(f"❌ Week {week_number}, {season_year} not found.")
            return

        games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()
        participants = Participant.query.all()

        for p in participants:
            if not p.telegram_chat_id:
                continue

            for g in games:
                local_time = g.game_time.replace(tzinfo=ZoneInfo("UTC")).astimezone(
                    ZoneInfo("America/Los_Angeles")
                )
                text = f"{g.away_team} @ {g.home_team}\n{local_time.strftime('%a %b %d %I:%M %p PT')}"
                try:
                    keyboard = [
                        [
                            InlineKeyboardButton(g.away_team, callback_data=f"{g.id}_{g.away_team}"),
                            InlineKeyboardButton(g.home_team, callback_data=f"{g.id}_{g.home_team}"),
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    resp = httpx.post(
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "chat_id": p.telegram_chat_id,
                            "text": text,
                            "reply_markup": reply_markup.to_dict(),
                        },
                    )
                    resp.raise_for_status()
                    logger.info(f"✅ Sent game to {p.name}: {text}")
                except Exception as e:
                    logger.error(f"❌ Failed to send game to {p.name}: {e}")

async def handle_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = create_app()
    with app.app_context():
        query = update.callback_query
        await query.answer()
        data = query.data
        game_id, team = data.split("_", 1)

        participant = Participant.query.filter_by(telegram_chat_id=query.message.chat_id).first()
        if not participant:
            await query.edit_message_text("❌ Participant not found.")
            return

        pick = Pick.query.filter_by(participant_id=participant.id, game_id=game_id).first()
        if pick:
            pick.selected_team = team
        else:
            pick = Pick(participant_id=participant.id, game_id=game_id, selected_team=team)
            db.session.add(pick)

        db.session.commit()
        await query.edit_message_text(f"✅ You picked {team}")

async def handle_send_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if len(args) != 2:
            await update.message.reply_text("Usage: /sendweek <week_number> <season_year>")
            return
        week_number, season_year = map(int, args)
        send_week_games(week_number, season_year)
        await update.message.reply_text(f"✅ Week {week_number} games sent.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_my_picks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = create_app()
    with app.app_context():
        participant = Participant.query.filter_by(telegram_chat_id=update.message.chat_id).first()
        if not participant:
            await update.message.reply_text("❌ Participant not found.")
            return

        picks = Pick.query.filter_by(participant_id=participant.id).all()
        if not picks:
            await update.message.reply_text("No picks made yet.")
            return

        text = "\n".join([f"{p.game.away_team} @ {p.game.home_team}: {p.selected_team}" for p in picks])
        await update.message.reply_text(text)

def reset_picks_for_participant(name):
    app = create_app()
    with app.app_context():
        participant = Participant.query.filter_by(name=name).first()
        if not participant:
            logger.error(f"❌ Participant {name} not found.")
            return
        Pick.query.filter_by(participant_id=participant.id).delete()
        db.session.commit()
        logger.info(f"✅ Picks reset for {name}")

def run_telegram_listener():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("sendweek", handle_send_week))
    application.add_handler(CommandHandler("mypicks", handle_my_picks))
    application.add_handler(CallbackQueryHandler(handle_pick))

    logger.info("🤖 Telegram bot listener started...")
    application.run_polling()


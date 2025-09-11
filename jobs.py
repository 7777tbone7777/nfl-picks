import os
import logging
from flask import current_app
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from models import db, Participant, Week, Pick, Game, Reminder

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Telegram Bot setup
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# =========================
# Basic Message Sender
# =========================
def send_telegram_message(participant: Participant, text: str):
    """Send a Telegram DM to a participant."""
    if not participant.telegram_chat_id:
        logger.error(f"❌ No telegram_chat_id for {participant.name}")
        return False

    try:
        bot.send_message(chat_id=participant.telegram_chat_id, text=text, parse_mode="HTML")
        logger.info(f"✅ Sent Telegram message to {participant.name}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send Telegram message to {participant.name}: {e}")
        return False

# =========================
# Jobs: Week Launch
# =========================
def send_week_launch_sms(week: Week):
    """Send out weekly picks reminder via Telegram (renamed from SMS)."""
    participants = Participant.query.all()
    for p in participants:
        message = (
            f"🏈 NFL Picks - Week {week.week_number}, {week.season_year}\n\n"
            f"Games are live in the app. Submit your picks before the deadline!\n\n"
            f"Deadline: {week.picks_deadline.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        send_telegram_message(p, message)

    db.session.add(Reminder(week_id=week.id, channel="telegram", message="Week launch sent"))
    db.session.commit()
    logger.info(f"✅ Week {week.week_number} launch messages sent via Telegram.")

# =========================
# Jobs: Results
# =========================
def calculate_and_send_results():
    """Calculate weekly results and send to participants."""
    week = Week.query.order_by(Week.week_number.desc()).first()
    if not week:
        logger.warning("⚠️ No week found for results calculation")
        return

    results = []
    for p in Participant.query.all():
        wins = db.session.query(db.func.count(Pick.id)).join(Game).filter(
            Pick.participant_id == p.id,
            Game.week_id == week.id,
            Game.winner.isnot(None),
            Pick.selected_team == Game.winner,
        ).scalar() or 0
        losses = db.session.query(db.func.count(Pick.id)).join(Game).filter(
            Pick.participant_id == p.id,
            Game.week_id == week.id,
            Game.winner.isnot(None),
            Pick.selected_team != Game.winner,
        ).scalar() or 0
        results.append((p.name, wins, losses))

    # Sort by wins, then losses
    results.sort(key=lambda r: (-r[1], r[2]))

    standings_msg = f"📊 Results for Week {week.week_number}, {week.season_year}\n\n"
    for name, wins, losses in results:
        standings_msg += f"{name}: {wins}W - {losses}L\n"

    for p in Participant.query.all():
        send_telegram_message(p, standings_msg)

    db.session.add(Reminder(week_id=week.id, channel="telegram", message="Results sent"))
    db.session.commit()
    logger.info(f"✅ Results sent for Week {week.week_number}")

# =========================
# Telegram Listener (Optional)
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    username = update.effective_user.username
    tg_id = update.effective_chat.id
    participant = Participant.query.filter_by(name="Tony").first()  # temporary single-user binding

    if participant:
        participant.telegram_chat_id = str(tg_id)
        db.session.commit()
        await update.message.reply_text(f"✅ Linked your Telegram to {participant.name}")
    else:
        await update.message.reply_text("⚠️ You are not registered in the NFL Picks system.")

def run_telegram_listener():
    """Run bot listener loop (manual run: heroku run python -c "from jobs import run_telegram_listener; run_telegram_listener()")."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    logger.info("🤖 Telegram listener started. Waiting for commands...")
    application.run_polling()


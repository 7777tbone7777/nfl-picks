import os
import logging
from zoneinfo import ZoneInfo
import httpx

from flask_app import create_app
from models import db, Participant, Week, Game, Pick

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jobs")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

def _pt(dt_utc):
    """Convert naive UTC datetime to America/Los_Angeles for display."""
    return dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/Los_Angeles"))

def send_week_games(week_number: int, season_year: int = 2025):
    """Send Week games with inline buttons to all participants who have telegram_chat_id."""
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
                local_time = _pt(g.game_time)
                text = f"{g.away_team} @ {g.home_team}\n{local_time.strftime('%a %b %d %I:%M %p PT')}"
                # Use raw JSON for the inline keyboard (no python-telegram-bot classes here)
                reply_markup = {
                    "inline_keyboard": [[
                        {"text": g.away_team, "callback_data": f"pick:{g.id}:{g.away_team}"},
                        {"text": g.home_team, "callback_data": f"pick:{g.id}:{g.home_team}"},
                    ]]
                }
                try:
                    resp = httpx.post(
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "chat_id": str(p.telegram_chat_id),
                            "text": text,
                            "reply_markup": reply_markup,
                            "parse_mode": "HTML",
                        },
                        timeout=15,
                    )
                    resp.raise_for_status()
                    logger.info(f"✅ Sent game to {p.name}: {g.away_team} @ {g.home_team}")
                except Exception as e:
                    logger.exception(f"💥 Error sending game to {p.name}: {e}")

# --- Telegram listener (polling) ---
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram import Update

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    username = user.username or user.first_name or "unknown"
    logger.info(f"📩 /start from {username} (chat_id={chat_id})")

    # Try linking by existing participant with empty chat_id and same name as your group (Tony/Kevin/Will)
    link_candidates = [username, user.first_name, user.full_name]
    link_candidates = [c for c in link_candidates if c]

    app = create_app()
    linked = False
    with app.app_context():
        # Prefer exact match on Tony/Kevin/Will
        for candidate in link_candidates:
            p = Participant.query.filter_by(name=candidate).first()
            if p:
                p.telegram_chat_id = str(chat_id)
                db.session.commit()
                logger.info(f"🔗 Linked participant '{p.name}' to chat_id {chat_id}")
                linked = True
                break

    msg = "👋 Welcome! You’re registered to receive NFL picks." if linked else \
          f"👋 Welcome! Your chat_id is {chat_id}. Tell Tony to link this to your name."
    await update.message.reply_text(msg)

async def handle_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    try:
        _, game_id_str, team = query.data.split(":", 2)
        game_id = int(game_id_str)
    except Exception:
        await query.edit_message_text("⚠️ Invalid selection payload.")
        return

    chat_id = str(update.effective_chat.id)

    app = create_app()
    with app.app_context():
        participant = Participant.query.filter_by(telegram_chat_id=chat_id).first()
        if not participant:
            await query.edit_message_text("⚠️ Not linked yet. Send /start first.")
            return

        pick = Pick.query.filter_by(participant_id=participant.id, game_id=game_id).first()
        if not pick:
            pick = Pick(participant_id=participant.id, game_id=game_id, selected_team=team)
            db.session.add(pick)
        else:
            pick.selected_team = team
        db.session.commit()

    await query.edit_message_text(f"✅ You picked {team}")

def run_telegram_listener():
    """Run polling listener so /start and button taps are processed."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_pick))
    application.run_polling()

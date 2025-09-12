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
    """Format a UTC datetime in a friendly way (US/Eastern as example)."""
    if not dt_utc:
        return ""
    try:
        eastern = dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("US/Eastern"))
        return eastern.strftime("%a %b %-d @ %-I:%M %p ET")
    except Exception:
        return str(dt_utc)

async def start(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    user = update.effective_user
    chat_id = str(update.effective_chat.id)
    username = (user.username or "").strip()
    full_name = (getattr(user, "full_name", None) or "").strip()
    first_name = (user.first_name or "").strip()
    logger.info(f"📩 /start from {username or full_name or first_name or 'unknown'} (chat_id={chat_id})")

    app = create_app()
    with app.app_context():
        # Already linked?
        existing = Participant.query.filter_by(telegram_chat_id=chat_id).first()
        if existing:
            msg = f"👋 You're already registered as {existing.name}."
            await update.message.reply_text(msg)
            return

        # Try to link to existing participant by name candidates
        linked = None
        candidates = [n for n in {username, full_name, first_name} if n]
        for c in candidates:
            p = Participant.query.filter_by(name=c).first()
            if p:
                p.telegram_chat_id = chat_id
                db.session.commit()
                linked = p
                logger.info(f"🔗 Linked participant '{p.name}' to chat_id {chat_id}")
                break

        if not linked:
            # Create new participant record with a unique name based on Telegram profile
            base = full_name or username or first_name or f"user_{chat_id}"
            name = base
            suffix = 1
            while Participant.query.filter_by(name=name).first():
                suffix += 1
                name = f"{base} ({suffix})"
            p = Participant(name=name, telegram_chat_id=chat_id)
            db.session.add(p)
            db.session.commit()
            linked = p
            logger.info(f"🆕 Created participant '{name}' for chat_id {chat_id}")

    await update.message.reply_text(f"✅ Registered as {linked.name}. You're ready to make picks!")

async def handle_pick(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
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

    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes  # local import to avoid import-time failures
    from telegram import Update

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_pick))
    application.run_polling()

def _send_message(chat_id: str, text: str, reply_markup: dict | None = None):
    """Low-level helper to send a message via Telegram HTTP API (sync call)."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    data = {"chat_id": chat_id, "text": text}
    if reply_markup:
        data["reply_markup"] = reply_markup
    with httpx.Client(timeout=20) as client:
        resp = client.post(f"{TELEGRAM_API_URL}/sendMessage", data=data)
        resp.raise_for_status()

def send_week_games(week_number: int, season_year: int):
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

        participants = Participant.query.filter(Participant.telegram_chat_id.isnot(None)).all()
        for part in participants:
            chat_id = str(part.telegram_chat_id)
            for g in games:
                kb = {
                    "inline_keyboard": [
                        [{"text": g.away_team, "callback_data": f"pick:{g.id}:{g.away_team}"}],
                        [{"text": g.home_team, "callback_data": f"pick:{g.id}:{g.home_team}"}],
                    ]
                }
                text = f"{g.away_team} @ {g.home_team}\n{_pt(g.game_time)}"
                try:
                    _send_message(chat_id, text, reply_markup=kb)
                    logger.info(f"✅ Sent game to {part.name}: {g.away_team} @ {g.home_team}")
                except Exception as e:
                    logger.exception("❌ Failed to send game message: %s", e)


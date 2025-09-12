import os
import logging
from zoneinfo import ZoneInfo
import httpx
import os, asyncio, logging
import json
from telegram.ext import CommandHandler
from sqlalchemy import text

from flask_app import create_app
from models import db, Participant, Week, Game, Pick

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jobs")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_USER_IDS","").replace(" ","").split(",") if x.isdigit()}


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
    application.add_handler(CommandHandler("sendweek", sendweek_command))
    application.run_polling()

def _send_message(chat_id: str, text: str, reply_markup: dict | None = None):
    """Low-level helper to send a message via Telegram HTTP API (sync call)."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
# inside _send_message(...)
data = {"chat_id": chat_id, "text": text}
if reply_markup is not None:
    data["reply_markup"] = (
        reply_markup if isinstance(reply_markup, str) else json.dumps(reply_markup)
    )
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

# --- /sendweek admin command (additive, with DRY and ME) ---
async def sendweek_command(update, context):
    """
    Usage:
      /sendweek <week>            -> send to ALL (existing behavior)
      /sendweek <week> dry        -> DRY-RUN (no sends; report counts)
      /sendweek <week> me         -> send ONLY to the caller (admin)
      /sendweek <week> <name...>  -> send ONLY to that participant by name
    """
    import asyncio
    from sqlalchemy import text

    user = update.effective_user
    if ADMIN_IDS and (not user or user.id not in ADMIN_IDS):
        if update.message:
            await update.message.reply_text("Sorry, admin only.")
        return

    args = context.args or []
    if not args or not args[0].isdigit():
        if update.message:
            await update.message.reply_text("Usage: /sendweek <week_number> [dry|me|<participant name>]")
        return
    week_number = int(args[0])
    target = "all" if len(args) == 1 else " ".join(args[1:]).strip()

    # Helper: send to a single participant id/chat, only unpicked for that week
    def _send_to_one(participant_id: int, chat_id: str, season_year: int):
        # get only unpicked games for this participant
        rows = db.session.execute(text("""
            select g.id, g.away_team, g.home_team
            from games g
            join weeks w on w.id=g.week_id
            left join picks p on p.game_id=g.id and p.participant_id=:pid
            where w.season_year=:y and w.week_number=:w
              and (p.id is null or p.selected_team is null)
            order by g.game_time nulls last, g.id
        """), {"pid": participant_id, "y": season_year, "w": week_number}).mappings().all()

        sent = 0
        for g in rows:
            kb = {
                "inline_keyboard": [
                    [{"text": g["away_team"], "callback_data": f"pick:{g['id']}:{g['away_team']}"}],
                    [{"text": g["home_team"], "callback_data": f"pick:{g['id']}:{g['home_team']}"}],
                ]
            }
            _send_message(str(chat_id), f"{g['away_team']} @ {g['home_team']}", reply_markup=kb)
            sent += 1
        return sent

    # Targeted modes (dry/me/name) should NOT auto-create weeks; only use existing week.
    def _find_existing_week():
        return Week.query.filter_by(week_number=week_number).order_by(Week.season_year.desc()).first()

    # Handle targeted modes inline; keep broadcast in a background thread
    if target.lower() in ("dry", "me") or target.lower() not in ("all",):
        app = create_app()
        with app.app_context():
            wk = _find_existing_week()
            if not wk:
                if update.message:
                    await update.message.reply_text(f"Week {week_number} not found yet. (Dry/me/name modes do not auto-create.)")
                return
            season_year = wk.season_year

            if target.lower() == "dry":
                # Count how many messages would be sent to all registered participants
                people = db.session.execute(text("""
                    select id, name, telegram_chat_id
                    from participants
                    where telegram_chat_id is not null
                """)).mappings().all()
                total_msgs = 0
                for u in people:
                    cnt = db.session.execute(text("""
                        select count(*)
                        from games g
                        join weeks w on w.id=g.week_id
                        left join picks p on p.game_id=g.id and p.participant_id=:pid
                        where w.season_year=:y and w.week_number=:w
                          and (p.id is null or p.selected_team is null)
                    """), {"pid": u["id"], "y": season_year, "w": week_number}).scalar()
                    total_msgs += int(cnt or 0)
                await update.message.reply_text(
                    f"DRY RUN: would send {total_msgs} button message(s) to {len(people)} participant(s) for Week {week_number} ({season_year})."
                )
                return

            if target.lower() == "me":
                me_chat = str(update.effective_chat.id)
                person = db.session.execute(text("""
                    select id, telegram_chat_id from participants
                    where telegram_chat_id = :c
                """), {"c": me_chat}).mappings().first()
                if not person:
                    await update.message.reply_text("You're not linked yet. Send /start first.")
                    return
                sent = _send_to_one(person["id"], person["telegram_chat_id"], season_year)
                await update.message.reply_text(f"✅ Sent {sent} unpicked game(s) for Week {week_number} to you.")
                return

            # Otherwise: treat target as a participant name
            name = target
            person = db.session.execute(text("""
                select id, name, telegram_chat_id from participants
                where lower(name)=lower(:n)
            """), {"n": name}).mappings().first()
            if not person:
                await update.message.reply_text(f"Participant '{name}' not found.")
                return
            if not person["telegram_chat_id"]:
                await update.message.reply_text(f"Participant '{name}' has no Telegram chat linked. Ask them to /start.")
                return
            sent = _send_to_one(person["id"], person["telegram_chat_id"], season_year)
            await update.message.reply_text(f"✅ Sent {sent} unpicked game(s) for Week {week_number} to {person['name']}.")
            return

    # Default: broadcast to ALL (unchanged behavior; may create the week if missing)
    async def _do_broadcast():
        app = create_app()
        with app.app_context():
            wk = Week.query.filter_by(week_number=week_number).order_by(Week.season_year.desc()).first()
            if not wk:
                # best-effort create if missing
                import datetime as dt
                from nfl_data import fetch_and_create_week
                season_year = dt.datetime.utcnow().year
                fetch_and_create_week(week_number, season_year)
                wk = Week.query.filter_by(week_number=week_number).order_by(Week.season_year.desc()).first()
            season_year = wk.season_year
            send_week_games(week_number=week_number, season_year=season_year)

    if update.message:
        await update.message.reply_text(f"Sending Week {week_number} to all registered participants…")
    await asyncio.to_thread(_do_broadcast)
    if update.message:
        await update.message.reply_text("✅ Done.")


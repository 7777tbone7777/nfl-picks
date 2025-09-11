import os
import logging
from datetime import datetime
import httpx
from flask_app import create_app
from models import db, Week, Game, Participant, Pick

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_API_URL = f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_BOT_TOKEN')}"

def send_week_games(week_number, season_year):
    """Send all games for a given week/season to registered participants."""
    app = create_app()
    with app.app_context():
        week = Week.query.filter_by(number=week_number, season_year=season_year).first()
        if not week:
            logger.error(f"Week {week_number} {season_year} not found")
            return

        games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()
        participants = Participant.query.all()

        for p in participants:
            if not p.telegram_chat_id:
                continue

            for g in games:
                # Convert UTC → PT
                local_time = g.game_time.replace(
                    tzinfo=__import__("zoneinfo").ZoneInfo("UTC")
                ).astimezone(
                    __import__("zoneinfo").ZoneInfo("America/Los_Angeles")
                )

                # Build game message
                text = f"{g.away_team} @ {g.home_team}\n{local_time.strftime('%a %b %d %I:%M %p PT')}"

                # Add inline keyboard for picks
                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": g.away_team, "callback_data": f"{g.id}:{g.away_team}"},
                            {"text": g.home_team, "callback_data": f"{g.id}:{g.home_team}"},
                        ]
                    ]
                }

                try:
                    resp = httpx.post(
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "chat_id": p.telegram_chat_id,
                            "text": text,
                            "reply_markup": keyboard,
                        },
                    )
                    resp.raise_for_status()
                    logger.info(f"✅ Sent game to {p.name}: {text}")
                except Exception as e:
                    logger.error(f"❌ Error sending game to {p.name}: {e}")


def handle_pick(update, context):
    """Handles Telegram inline button picks and saves them in DB."""
    data = update.callback_query.data
    try:
        game_id, team = data.split(":", 1)

        app = create_app()
        with app.app_context():
            participant = Participant.query.filter_by(
                telegram_chat_id=update.effective_user.id
            ).first()

            if not participant:
                update.callback_query.answer("You are not registered.")
                return

            pick = Pick.query.filter_by(
                participant_id=participant.id, game_id=game_id
            ).first()

            if pick:
                pick.selected_team = team
            else:
                pick = Pick(
                    participant_id=participant.id,
                    game_id=game_id,
                    selected_team=team,
                )
                db.session.add(pick)

            db.session.commit()
            update.callback_query.answer(f"You picked {team}")
    except Exception as e:
        logger.error(f"❌ Error handling pick: {e}")
        update.callback_query.answer("Something went wrong saving your pick.")


def reset_picks_for_participant(participant_name):
    """Delete all picks for a participant (for testing)."""
    app = create_app()
    with app.app_context():
        participant = Participant.query.filter_by(name=participant_name).first()
        if not participant:
            logger.error(f"Participant {participant_name} not found")
            return

        Pick.query.filter_by(participant_id=participant.id).delete()
        db.session.commit()
        logger.info(f"✅ Picks reset for {participant_name}")


def run_telegram_listener():
    """Start the Telegram listener (used in worker dyno)."""
    from telegram.ext import Updater, CallbackQueryHandler

    updater = Updater(os.environ.get("TELEGRAM_BOT_TOKEN"))
    dp = updater.dispatcher

    dp.add_handler(CallbackQueryHandler(handle_pick))

    logger.info("🤖 Telegram bot listener started...")
    updater.start_polling()
    updater.idle()


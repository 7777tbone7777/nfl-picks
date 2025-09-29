import logging
import os
from datetime import datetime  # Added for general date/time use
from zoneinfo import ZoneInfo  # Added for timezone conversion

import httpx  # Changed from 'h px'
from app import create_app  # Changed from 'flask_app' to 'app' as per app.py
from sqlalchemy import and_  # Added for calculating results
from telegram import InlineKeyboardButton  # Corrected imports
from telegram import InlineKeyboardMarkup, ParseMode, Update
from telegram.ext import Application  # Corrected imports
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from models import Game, Participant, Pick, Week, db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jobs")

# Ensure TELEGRAM_BOT_TOKEN is set in your environment variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    logger.error(
        "TELEGRAM_BOT_TOKEN environment variable not set. Telegram features will not work."
    )
    # Exit or raise error if token is critical for execution
    # For now, we'll just log and continue, but messages won't send.

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_week_games(week_number: int, season_year: int) -> None:
    """
    Fetches games for a given week and sends them to all participants
    with inline pick buttons via Telegram.
    """
    app = create_app()
    with app.app_context():
        week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()

        if not week:
            logger.error(f"❌ No week found for {season_year} week {week_number}")
            return

        participants = Participant.query.all()
        games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()

        if not games:
            logger.warning(
                f"No games found for week {week_number}, season {season_year}. Skipping sending games."
            )
            return

        for p in participants:
            if not p.telegram_chat_id:
                logger.info(f"Skipping {p.name}: No Telegram chat ID found.")
                continue

            logger.info(
                f"Sending Week {week_number} games to {p.name} (chat_id: {p.telegram_chat_id})..."
            )

            for g in games:
                # Convert game time to Pacific Time (PT) for display
                # g.game_time is naive UTC, so make it aware first
                game_time_utc = g.game_time.replace(tzinfo=ZoneInfo("UTC"))
                local_time_pt = game_time_utc.astimezone(ZoneInfo("America/Los_Angeles"))

                # Check if the pick has already been made by this participant for this game
                existing_pick = Pick.query.filter_by(participant_id=p.id, game_id=g.id).first()

                # Text for the message
                text = f"{g.away_team} @ {g.home_team}\n{local_time_pt.strftime('%a %b %d %I:%M %p PT')}"

                # If a pick already exists, indicate it
                if existing_pick:
                    text += f"\n*Your current pick: {existing_pick.selected_team}*"

                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                g.away_team,
                                callback_data=f"pick:{p.id}:{g.id}:{g.away_team}",
                            ),
                            InlineKeyboardButton(
                                g.home_team,
                                callback_data=f"pick:{p.id}:{g.id}:{g.home_team}",
                            ),
                        ]
                    ]
                )

                try:
                    resp = httpx.post(  # Corrected client to httpx
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "chat_id": p.telegram_chat_id,
                            "text": text,
                            "reply_markup": keyboard.to_dict(),
                            "parse_mode": "Markdown",  # Use Markdown to bold the current pick
                        },
                        timeout=10,  # Add a timeout for robustness
                    )
                    resp.raise_for_status()  # Raises an exception for 4xx/5xx responses
                    logger.info(f"✅ Sent game to {p.name}: {g.away_team} @ {g.home_team}")
                except httpx.HTTPStatusError as e:
                    logger.error(
                        f"💥 HTTP Error sending game to {p.name} (chat_id: {p.telegram_chat_id}): {e.response.status_code} - {e.response.text}"
                    )
                except httpx.RequestError as e:
                    logger.error(
                        f"💥 Request Error sending game to {p.name} (chat_id: {p.telegram_chat_id}): {e}"
                    )
                except Exception as e:
                    logger.error(
                        f"💥 Unexpected Error sending game to {p.name} (chat_id: {p.telegram_chat_id}): {e}"
                    )


async def handle_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles inline keyboard button presses for making picks.
    """
    query = update.callback_query
    if not query:
        return

    await query.answer()  # Acknowledge the callback query

    try:
        # data format: "pick:participant_id:game_id:team"
        _, participant_id_str, game_id_str, team = query.data.split(":", 3)  # Use 3 for maxsplit
        participant_id = int(participant_id_str)
        game_id = int(game_id_str)
    except ValueError:
        logger.error(f"💥 Invalid callback data format: {query.data}")
        await query.edit_message_text("❌ Error processing pick. Invalid data format.")
        return

    app = create_app()
    with app.app_context():
        # Check if picks deadline has passed for this game's week
        game = Game.query.get(game_id)
        if not game:
            logger.error(f"Game with ID {game_id} not found for pick by {participant_id}.")
            await query.edit_message_text("❌ Error: Game not found.")
            return

        week = Week.query.get(game.week_id)
        if week and datetime.utcnow() > week.picks_deadline:
            await query.edit_message_text(
                f"❌ Deadline for Week {week.week_number} has passed. Cannot change pick."
            )
            return

        pick = Pick.query.filter_by(participant_id=participant_id, game_id=game_id).first()
        if not pick:
            # Note: The model column is 'selected_team', not 'team'
            pick = Pick(participant_id=participant_id, game_id=game_id, selected_team=team)
            db.session.add(pick)
            logger.info(
                f"Created new pick for participant {participant_id}, game {game_id}: {team}"
            )
        else:
            old_team = pick.selected_team
            pick.selected_team = team
            logger.info(
                f"Updated pick for participant {participant_id}, game {game_id}: {old_team} -> {team}"
            )

        try:
            db.session.commit()
            await query.edit_message_text(f"✅ Pick saved: You picked *{team}*")
        except Exception as e:
            db.session.rollback()
            logger.error(
                f"💥 Error saving pick for participant {participant_id}, game {game_id}: {e}"
            )
            await query.edit_message_text("❌ Error saving your pick to the database.")


# NEW START HANDLER
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the /start command. Links user's Telegram chat ID to a participant
    if their Telegram username matches a participant's name.
    """
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info(f"📩 /start received from {user.username} (id={chat_id})")

    if not user.username:
        await update.message.reply_text(
            "👋 Welcome! To link your Telegram account, please set a public Telegram username in your Telegram settings."
        )
        return

    app = create_app()
    with app.app_context():
        # Attempt to find participant by Telegram username matching participant name
        # Note: This assumes participant.name in DB is the Telegram username.
        # You might need a more robust linking mechanism (e.g., a unique code).
        participant = Participant.query.filter_by(name=user.username).first()

        if participant:
            participant.telegram_chat_id = str(chat_id)
            try:
                db.session.commit()
                logger.info(
                    f"🔗 Linked participant '{participant.name}' (DB ID: {participant.id}) to Telegram chat_id {chat_id}"
                )
                await update.message.reply_text(
                    f"👋 Welcome {participant.name}! Your Telegram account is now linked. You'll receive game picks and reminders here."
                )
            except Exception as e:
                db.session.rollback()
                logger.error(
                    f"💥 Error linking participant {participant.name} to chat_id {chat_id}: {e}"
                )
                await update.message.reply_text(
                    "❌ Error linking your Telegram account. Please try again or contact support."
                )
        else:
            logger.info(f"🤷‍♀️ No participant found for Telegram username '{user.username}'.")
            await update.message.reply_text(
                f"👋 Welcome! I couldn't find a registered participant for the Telegram username '{user.username}'. "
                "Please make sure your Telegram username matches the name you registered with, or contact the pool administrator."
            )


async def send_notification_telegram(
    chat_id: str, message: str, parse_mode: str = ParseMode.HTML
) -> bool:
    """Helper function to send a Telegram message to a specific chat_id."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set, cannot send Telegram message.")
        return False

    try:
        resp = httpx.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": parse_mode,
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"✅ Telegram message sent to chat_id {chat_id}.")
        return True
    except httpx.HTTPStatusError as e:
        logger.error(
            f"💥 HTTP Error sending Telegram notification to {chat_id}: {e.response.status_code} - {e.response.text}"
        )
    except httpx.RequestError as e:
        logger.error(f"💥 Request Error sending Telegram notification to {chat_id}: {e}")
    except Exception as e:
        logger.error(f"💥 Unexpected Error sending Telegram notification to {chat_id}: {e}")
    return False


# --- Refactored notification functions to use Telegram ---


def send_week_launch_notification(week_number: int, app_instance):
    """Sends a launch notification for a new week to all participants via Telegram."""
    with app_instance.app_context():
        participants = Participant.query.all()
        for p in participants:
            if not p.telegram_chat_id:
                logger.warning(f"Skipping launch notification for {p.name}: No Telegram chat ID.")
                continue

            # Assuming 'app_instance' refers to the Flask app, and you can generate URLs
            # Make sure this `url_for` is correctly configured if it's used in a non-request context.
            # For this context, it will likely generate relative paths, which might not be external.
            # If your picks form is *only* via Telegram buttons, this URL might not be needed.
            # If it's a web form, ensure `_external=True` and `SERVER_NAME` is set in Flask app config.
            # For simplicity, I'll remove the external URL for now unless specifically requested.
            # url = url_for('picks_form', week_number=week_number, participant_name=p.name.lower(), _external=True)
            # message = f"NFL Picks Week {week_number} is live! Make your picks: {url}"

            message = f"🏈 Week {week_number} of NFL Picks is *LIVE*! Time to make your selections. I'll send you each game individually."
            send_notification_telegram(p.telegram_chat_id, message, parse_mode=ParseMode.MARKDOWN)

            # After sending the launch message, send the individual games
            send_week_games(week_number, datetime.now().year)  # Assuming current year for new week


def check_and_send_reminders(app_instance):
    """
    Checks for unpicked games for participants and sends reminders via Telegram.
    """
    with app_instance.app_context():
        now = datetime.utcnow()
        current_week = (
            Week.query.filter(Week.picks_deadline > now).order_by(Week.week_number).first()
        )
        if not current_week:
            logger.info("No current week for reminders.")
            return

        games_count = Game.query.filter_by(week_id=current_week.id).count()
        if games_count == 0:
            logger.warning(
                f"No games found for current week {current_week.week_number}. Skipping reminders."
            )
            return

        participants = Participant.query.all()
        for p in participants:
            if not p.telegram_chat_id:
                logger.warning(f"Skipping reminder for {p.name}: No Telegram chat ID found.")
                continue

            picks_count = (
                Pick.query.filter_by(participant_id=p.id)
                .join(Game)
                .filter(Game.week_id == current_week.id)
                .count()
            )
            if picks_count < games_count:
                hours_left = (current_week.picks_deadline - now).total_seconds() / 3600
                reminder_kind = (
                    "deadline" if hours_left <= 48 else "nudge"
                )  # Use 'kind' from Reminder model

                # Check if this specific reminder has already been sent for this kind and participant
                if not Reminder.query.filter_by(
                    participant_id=p.id,
                    week_id=current_week.id,
                    kind=reminder_kind,
                    channel="telegram",
                ).first():
                    missing_count = games_count - picks_count

                    message = f"🔔 *Reminder {p.name}*! You still have *{missing_count}* games unpicked for Week {current_week.week_number}. The deadline is approaching!"
                    # For Telegram, we'll re-send the game buttons if they haven't picked.
                    # Or a direct message asking them to check.
                    # For simplicity, let's just send the reminder message.
                    # If you want to resend buttons for *only* unpicked games, that's more complex.

                    if send_notification_telegram(
                        p.telegram_chat_id, message, parse_mode=ParseMode.MARKDOWN
                    ):
                        db.session.add(
                            Reminder(
                                participant_id=p.id,
                                week_id=current_week.id,
                                kind=reminder_kind,
                                channel="telegram",
                                status="sent",
                            )
                        )
        db.session.commit()
        logger.info("Reminder check complete.")


def calculate_and_send_results(app_instance):
    """
    Calculates weekly results for the latest week and sends them to participants via Telegram.
    """
    with app_instance.app_context():
        latest_week = Week.query.order_by(Week.week_number.desc()).first()
        if not latest_week:
            logger.info("No weeks found to calculate results.")
            return

        week_to_score = latest_week.week_number
        season_to_score = latest_week.season_year

        # Make sure scores are up-to-date before calculating results
        try:
            from nfl_data import update_scores_for_week

            logger.info(f"Updating scores for Week {week_to_score} before calculating results...")
            update_scores_for_week(week_to_score, season_to_score)
        except ImportError:
            logger.warning(
                "Warning: update_scores_for_week function not found. Skipping score updates."
            )
        except Exception as e:
            logger.error(f"Error updating scores for Week {week_to_score}: {e}")

        games = Game.query.filter_by(week_id=latest_week.id, status="final").all()

        # Update pick results (W/L) based on final game scores
        for game in games:
            winner = None
            if game.home_score is not None and game.away_score is not None:
                if game.home_score > game.away_score:
                    winner = game.home_team
                elif game.away_score > game.home_score:
                    winner = game.away_team

            # Ensure game.winner is set in the DB as well for dashboard/standings
            if winner and game.winner != winner:
                game.winner = winner  # Update the game model's winner field
                db.session.add(game)  # Mark for commit

            if winner is None:
                continue  # Skip games without a clear winner (e.g., ties, or not fully scored)

            for pick in Pick.query.filter_by(game_id=game.id).all():
                if pick.selected_team == winner:
                    pick.result = "W"
                else:
                    pick.result = "L"
                db.session.add(pick)  # Mark for commit

        db.session.commit()
        logger.info(f"Scored all final games for Week {week_to_score} and updated picks.")

        # Send results to participants
        for p in Participant.query.all():
            if not p.telegram_chat_id:
                logger.warning(f"Skipping results for {p.name}: No Telegram chat ID.")
                continue

            wins = (
                Pick.query.filter(
                    and_(
                        Pick.participant_id == p.id,
                        Pick.result == "W",
                        Game.week_id == latest_week.id,
                    )
                )
                .join(Game)
                .count()
            )  # Ensure Game.week_id filter is applied to the join

            losses = (
                Pick.query.filter(
                    and_(
                        Pick.participant_id == p.id,
                        Pick.result == "L",
                        Game.week_id == latest_week.id,
                    )
                )
                .join(Game)
                .count()
            )  # Ensure Game.week_id filter is applied to the join

            # Format results message
            message = (
                f"🎉 *NFL Picks Week {week_to_score} Results for {p.name}* 🎉\n"
                f"You finished with a record of *{wins} wins* and *{losses} losses*!"
            )

            # Optionally list specific picks and their results
            # user_picks_for_week = Pick.query.filter(
            #     and_(Pick.participant_id == p.id, Game.week_id == latest_week.id)
            # ).join(Game).all()
            # for up in user_picks_for_week:
            #     message += f"\n- {up.game.away_team} @ {up.game.home_team}: Picked {up.selected_team} ({up.result})"

            send_notification_telegram(p.telegram_chat_id, message, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"Sent results to {p.name} (chat_id: {p.telegram_chat_id}).")


def run_telegram_listener():
    """
    Initializes and starts the Telegram bot listener.
    This should run as a separate process or in a long-running service.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Cannot run Telegram listener: TELEGRAM_BOT_TOKEN not set.")
        return

    logger.info("Starting Telegram bot listener...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_pick))

    # Run the bot until the user presses Ctrl-C or the process receives a signal.
    application.run_polling(poll_interval=1)  # Set a small poll_interval for quicker response


# --- Old Twilio functions removed or commented out ---
# The original `send_sms` function is removed as we are fully moving to Telegram for notifications.
# The previous `send_week_launch_sms` has been replaced/adapted by `send_week_launch_notification`
# and the actual sending of games `send_week_games`.
# `check_and_send_reminders` and `calculate_and_send_results` are updated to use Telegram.

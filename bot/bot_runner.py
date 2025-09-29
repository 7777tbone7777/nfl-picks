import asyncio
import logging
import os
import traceback
from typing import Awaitable, Callable

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

# Your existing handlers module
from bot import telegram_handlers as th

# Create your Flask app with your existing factory
from wsgi import create_app  # must return a Flask app instance

# ---------- Logging ----------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("bot_runner")


# ---------- Error handler (so exceptions show up in chat) ----------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    tb = "".join(traceback.format_exception(None, err, err.__traceback__)) if err else "Unknown"
    logger.exception("Unhandled error in handler", exc_info=err)

    # Try to notify the user who triggered the error (best effort)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "⚠️ Oops, something broke on my side.\n\n"
                    "<b>Error (truncated):</b>\n"
                    f"<pre>{tb[-1500:]}</pre>"
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    except Exception:  # noqa: BLE001
        # Don't let secondary errors bubble
        logger.debug("Failed to send error message to user", exc_info=True)


# ---------- App-context wrapper ----------
def with_flask_app_context(
    app,  # Flask app instance
    fn: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
    """
    Wrap a PTB async handler so it always runs inside Flask's app_context().
    This avoids 'Working outside of application context.' for db/session/model use.
    """

    async def _wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Push a fresh app context for every callback invocation
        with app.app_context():
            return await fn(update, context)

    # Preserve helpful bits
    _wrapped.__name__ = getattr(fn, "__name__", "_wrapped")  # type: ignore[attr-defined]
    _wrapped.__doc__ = getattr(fn, "__doc__", None)
    return _wrapped


# ---------- Main ----------
async def main() -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable.")

    # Create Flask app once (not per update)
    flask_app = create_app()

    # (Optional) Let your handlers know about the Flask app, if they use it directly.
    # This is a no-op unless you've implemented th.init_app(app).
    if hasattr(th, "init_app") and callable(th.init_app):
        th.init_app(flask_app)

    application: Application = ApplicationBuilder().token(bot_token).build()

    # Register handlers — each wrapped so DB calls have a Flask app context.
    application.add_handler(CommandHandler("start", with_flask_app_context(flask_app, th.start)))
    application.add_handler(
        CommandHandler("mypicks", with_flask_app_context(flask_app, th.mypicks))
    )

    # Optional: a lightweight /help
    if hasattr(th, "help_command"):
        application.add_handler(
            CommandHandler("help", with_flask_app_context(flask_app, th.help_command))
        )

    # Global error handler
    application.add_error_handler(on_error)

    logger.info("Bot is starting run_polling() …")
    await application.run_polling(allowed_updates=None)  # PTB will infer from handlers


if __name__ == "__main__":
    asyncio.run(main())

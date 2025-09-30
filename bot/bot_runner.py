# bot/bot_runner.py
from __future__ import annotations

import logging
import os

from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

# 1) Build Flask app and push context BEFORE importing modules that touch db/models.
from flask_app import create_app  # <-- you referenced flask_app in your repo

flask_app = create_app()
flask_app.app_context().push()

# 2) Now it's safe to import handlers that may touch db/current_app
import bot.telegram_handlers as th  # noqa: E402
from bot.context import in_app_context  # noqa: E402


def build_application() -> Application:
    """Create the PTB Application with sane defaults."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    application = ApplicationBuilder().token(token).rate_limiter(AIORateLimiter()).build()

    # ---- Register handlers (specific commands FIRST) ----
    application.add_handler(CommandHandler("start", in_app_context(th.start)))
    application.add_handler(CommandHandler("seepicks", in_app_context(th.seepicks_command)))
    application.add_handler(CommandHandler("sendweek", in_app_context(th.sendweek_command)))
    application.add_handler(CommandHandler("mypicks", in_app_context(th.mypicks)))

    # Inline button callbacks for picking teams
    application.add_handler(CallbackQueryHandler(th.handle_pick))

    # Optional extras: add only if the functions exist to avoid AttributeError
    if hasattr(th, "ping"):
        application.add_handler(CommandHandler("ping", in_app_context(th.ping)))
    if hasattr(th, "unknown_command"):
        # Non-blocking so it doesn't swallow real commands
        application.add_handler(MessageHandler(filters.COMMAND, th.unknown_command, block=False))
    elif hasattr(th, "fallback"):
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, in_app_context(th.fallback))
        )

    return application


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = build_application()
    logging.getLogger(__name__).info("Starting bot polling…")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

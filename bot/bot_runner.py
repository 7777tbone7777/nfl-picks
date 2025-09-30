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
)

# Build Flask app and push context BEFORE importing modules that touch db/models.
from flask_app import create_app  # adjust if your factory lives elsewhere

flask_app = create_app()
flask_app.app_context().push()

# Now it's safe to import handlers that may touch db/current_app
import bot.telegram_handlers as th  # noqa: E402
from bot.context import in_app_context  # noqa: E402


def build_application() -> Application:
    """Create the PTB Application with sane defaults."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    application = (
        ApplicationBuilder()
        .token(token)
        .rate_limiter(AIORateLimiter())
        .build()
    )

    # ---- Register handlers (specific commands FIRST) ----
    application.add_handler(CommandHandler("start", in_app_context(th.start)))
    application.add_handler(CallbackQueryHandler(th.handle_pick))

    application.add_handler(CommandHandler("sendweek", in_app_context(th.sendweek_command)))
    application.add_handler(CommandHandler("syncscores", in_app_context(th.syncscores_command)))
    application.add_handler(CommandHandler("getscores", in_app_context(th.getscores_command)))
    application.add_handler(CommandHandler("seasonboard", in_app_context(th.seasonboard_command)))
    application.add_handler(CommandHandler("deletepicks", in_app_context(th.deletepicks_command)))
    application.add_handler(CommandHandler("whoisleft", in_app_context(th.whoisleft_command)))
    application.add_handler(CommandHandler("seepicks", in_app_context(th.seepicks_command)))
    application.add_handler(CommandHandler("remindweek", in_app_context(th.remindweek_command)))

    # Our local command (defined in telegram_handlers.py)
    application.add_handler(CommandHandler("mypicks", in_app_context(th.mypicks)))

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


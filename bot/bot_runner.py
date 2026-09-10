# bot/bot_runner.py
from __future__ import annotations
from bot.telegram_handlers import seasonboard_command

import logging
import os

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
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
        .post_init(_publish_command_menu)
        .build()
    )

    # ---- Register handlers (specific commands FIRST) ----
    application.add_handler(CommandHandler("start", in_app_context(th.start)))
    application.add_handler(CommandHandler("help", in_app_context(th.help_command)))
    # Pattern-based callback handlers for picks and props
    application.add_handler(CallbackQueryHandler(th.handle_pick, pattern="^pick:"))
    application.add_handler(CallbackQueryHandler(th.handle_prop_pick, pattern="^prop:"))

    application.add_handler(CommandHandler("sendweek", in_app_context(th.sendweek_command)))
    application.add_handler(CommandHandler("syncscores", in_app_context(th.syncscores_command)))
    application.add_handler(CommandHandler("getscores", in_app_context(th.getscores_command)))
    application.add_handler(CommandHandler("seasonboard", in_app_context(th.seasonboard_command)))
    application.add_handler(CommandHandler("deletepicks", in_app_context(th.deletepicks_command)))
    application.add_handler(CommandHandler("whoisleft", in_app_context(th.whoisleft_command)))
    application.add_handler(CommandHandler("seepicks", in_app_context(th.seepicks_command)))
    application.add_handler(CommandHandler("admin", in_app_context(th.admin_command)))
    application.add_handler(CommandHandler("remindweek", in_app_context(th.remindweek_command)))

    # Our local commands (defined in telegram_handlers.py)
    application.add_handler(CommandHandler("mypicks", in_app_context(th.mypicks)))
    application.add_handler(CommandHandler("myprops", in_app_context(th.myprops)))

    _warn_on_command_drift(application)

    return application


def _warn_on_command_drift(application: Application) -> None:
    """Log when bot/commands.py and the registered handlers disagree."""
    from bot.commands import check_drift

    registered = set()
    for group in application.handlers.values():
        for h in group:
            if isinstance(h, CommandHandler):
                registered.update(h.commands)

    log = logging.getLogger(__name__)
    for problem in check_drift(registered):
        log.warning("command drift: %s", problem)


async def _publish_command_menu(application: Application) -> None:
    """Populate the Telegram command menu.

    Participants see only the commands they can run. Admins get the full list
    through a per-chat scope. Publishing on startup means the menu tracks
    deploys instead of needing a manual BotFather update.
    """
    from bot.commands import telegram_admin_commands, telegram_user_commands

    log = logging.getLogger(__name__)
    try:
        await application.bot.set_my_commands(
            [BotCommand(n, d) for n, d in telegram_user_commands()],
            scope=BotCommandScopeDefault(),
        )
        admin_menu = [BotCommand(n, d) for n, d in telegram_admin_commands()]
        for admin_id in sorted(th.ADMIN_IDS):
            try:
                await application.bot.set_my_commands(
                    admin_menu, scope=BotCommandScopeChat(chat_id=admin_id)
                )
            except Exception:
                log.exception("Failed publishing admin menu to chat %s", admin_id)
        log.info(
            "Published command menu: %s public, %s admin, %s admin chat(s)",
            len(telegram_user_commands()),
            len(admin_menu),
            len(th.ADMIN_IDS),
        )
    except Exception:
        # A menu failure must never stop the bot from polling.
        log.exception("Failed publishing the command menu")


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request URL at INFO, and the Telegram API URL embeds the
    # bot token: .../bot<TOKEN>/getUpdates. At one poll per 10 seconds that
    # writes the token to the log thousands of times a day and buries every
    # other line. Warnings and errors still come through.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    app = build_application()
    logging.getLogger(__name__).info("Starting bot polling…")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()


# bot/bot_runner.py
from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Callable

from telegram import Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    filters,
)

# IMPORTANT: build Flask app first, push a *global* app_context
from wsgi import create_app

flask_app = create_app()
# Push once for the main event-loop thread (safe for PTB asyncio single-threaded loop)
flask_app.app_context().push()

# Now it is safe to import anything that touches `db`, models, current_app, etc.
from bot import telegram_handlers as th  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger("bot_runner")


def in_app_context(fn: Callable[[Update, CallbackContext], asyncio.Future]):
    """Decorator: ensure every handler runs inside Flask app_context."""

    async def wrapper(update: Update, context: CallbackContext):
        with flask_app.app_context():
            return await fn(update, context)

    return wrapper


def build_application() -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]

    app = (
        ApplicationBuilder()
        .token(token)
        .rate_limiter(AIORateLimiter())
        .concurrent_updates(False)  # process sequentially; simpler while debugging
        .build()
    )

    # Register handlers, *wrapped* in Flask context
    app.add_handler(CommandHandler("start", in_app_context(th.start)))
    app.add_handler(CommandHandler("mypicks", in_app_context(th.mypicks)))
    app.add_handler(CommandHandler("ping", in_app_context(th.ping)))  # lightweight sanity check
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, in_app_context(th.fallback)))

    return app


async def main():
    application = build_application()

    # Simple health log
    me = await application.bot.get_me()
    log.info("Bot ready as @%s (id=%s)", me.username, me.id)

    # Graceful stop hooks
    stop_event = asyncio.Event()

    def _graceful(*_):
        log.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _graceful)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _graceful())  # Windows

    # Poll all update types (no webhook)
    await application.initialize()
    await application.start()
    await application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        timeout=10,
    )

    await stop_event.wait()

    await application.updater.stop()
    await application.stop()
    await application.shutdown()
    log.info("Bot stopped cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

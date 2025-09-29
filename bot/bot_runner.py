from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from .config import load_config
from .logging_setup import setup_logging
from .telegram_handlers import handle_pick, mypicks, start


def main():
    setup_logging()
    cfg = load_config()
    if not cfg.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(cfg.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_pick, pattern=r"^pick:"))
    app.add_handler(CommandHandler("mypicks", mypicks))

    app.run_polling(allowed_updates=Application.ALL_UPDATE_TYPES)


if __name__ == "__main__":
    main()

# bot/context.py
from __future__ import annotations

from typing import Any, Awaitable, Callable


def in_app_context(handler: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """PTB v20+ compatible async wrapper.
    - MUST be async and MUST await the inner handler.
    - Place per-request setup/teardown here if needed (e.g., db sessions).
    """

    async def wrapper(update, context):
        # Example: attach services/DB to context if you want
        # db = context.application.bot_data.get("db") if hasattr(context.application, "bot_data") else None
        # context.chat_data["db"] = db

        try:
            return await handler(update, context)
        finally:
            # Example cleanup:
            # session = context.chat_data.pop("db_session", None)
            # if session:
            #     await session.aclose()
            pass

    return wrapper

import logging
from typing import List

import httpx

log = logging.getLogger("admin_alerts")


async def notify_admins(telegram_token: str, admin_chat_ids: List[int], text: str) -> None:
    if not telegram_token or not admin_chat_ids:
        return
    async with httpx.AsyncClient(timeout=15.0) as client:
        for chat_id in admin_chat_ids:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{telegram_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    },
                )
            except Exception as e:
                log.warning("Failed to notify admin %s: %s", chat_id, e)

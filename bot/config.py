
import os
from dataclasses import dataclass
from typing import List, Optional

@dataclass(frozen=True)
class BotConfig:
    telegram_bot_token: str
    admin_chat_ids: List[int]
    app_tz: str = "America/Los_Angeles"  # for local 'Tuesday' logic
    espn_timeout_s: float = 20.0
    espn_retries: int = 3
    espn_backoff_s: float = 1.5

def _parse_admin_ids(raw: Optional[str]) -> List[int]:
    vals: List[int] = []
    if not raw:
        return vals
    for part in raw.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            vals.append(int(part))
        except ValueError:
            continue
    return vals

def load_config() -> BotConfig:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    admins = _parse_admin_ids(os.getenv("ADMIN_IDS"))
    return BotConfig(
        telegram_bot_token=token,
        admin_chat_ids=admins,
    )

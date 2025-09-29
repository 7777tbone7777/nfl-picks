from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def parse_iso_to_aware_utc(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def is_tuesday_local(dt_utc: datetime, local_tz: str) -> bool:
    local = dt_utc.astimezone(ZoneInfo(local_tz))
    return local.weekday() == 1  # Monday=0, Tuesday=1


def local_fmt(dt_utc_aware: datetime, local_tz: str, fmt: str = "%a %m/%d %I:%M %p %Z") -> str:
    return dt_utc_aware.astimezone(ZoneInfo(local_tz)).strftime(fmt)

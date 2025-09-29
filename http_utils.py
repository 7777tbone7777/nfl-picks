import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

log = logging.getLogger("http_utils")


async def get_json_with_retry(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout_s: float = 20.0,
    retries: int = 3,
    backoff_s: float = 1.5,
) -> Optional[Dict[str, Any]]:
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            last_exc = e
            log.warning("GET %s failed (attempt %s/%s): %s", url, attempt, retries, e)
            if attempt < retries:
                await asyncio.sleep(backoff_s * attempt)
    log.error("GET %s ultimately failed after %s attempts: %s", url, retries, last_exc)
    return None

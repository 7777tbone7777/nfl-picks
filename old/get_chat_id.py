import os

import httpx

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
resp = httpx.get(url)
print(resp.json())

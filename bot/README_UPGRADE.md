
# NFL Picks Bot — Refactor Step 1

This kit implements the **first wave** of your improvements:

- Timezone handling with UTC-aware helpers (and a safe path for legacy naive UTC columns).
- ESPN client with retry/backoff (async httpx, no extra deps).
- Admin alerts to Telegram when ESPN returns 0 events during an expected import.
- Pick deadline enforcement (reject picks after kickoff).
- Centralized config + logging modules.
- Initial file split: `cron_jobs.py` and `telegram_handlers.py`.

## Files

- `config.py` — env-driven settings (`TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`, `app_tz`).
- `logging_setup.py` — one call to init logging.
- `time_utils.py` — UTC + local-time helpers.
- `http_utils.py` — robust GET with retry/backoff (async).
- `espn_client.py` — normalized ESPN fetcher.
- `admin_alerts.py` — DM admins on anomalies.
- `cron_jobs.py` — import upcoming week, sync latest active week.
- `telegram_handlers.py` — `/start`, `handle_pick` (with deadline), `/mypicks`.

## Integration

1. Copy these into a package in your project, e.g., `bot/`.
2. At process start:
   ```python
   from bot.logging_setup import setup_logging
   setup_logging()
   from bot.config import load_config
   cfg = load_config()
   ```
3. Register handlers (python-telegram-bot v20+):
   ```python
   from telegram.ext import Application, CommandHandler, CallbackQueryHandler
   from bot.telegram_handlers import start, handle_pick, mypicks

   app = Application.builder().token(cfg.telegram_bot_token).build()
   app.add_handler(CommandHandler("start", start))
   app.add_handler(CallbackQueryHandler(handle_pick, pattern=r"^pick:"))
   app.add_handler(CommandHandler("mypicks", mypicks))
   ```
4. Use cron tasks:
   ```python
   from bot.cron_jobs import cron_import_upcoming_week, cron_syncscores_latest_active
   print(cron_import_upcoming_week())
   print(cron_syncscores_latest_active())
   ```

## Recommended Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_games_week ON games(week_id);
CREATE INDEX IF NOT EXISTS idx_games_home_away ON games(lower(home_team), lower(away_team));
CREATE INDEX IF NOT EXISTS idx_picks_participant_game ON picks(participant_id, game_id);
CREATE INDEX IF NOT EXISTS idx_participants_chat ON participants(telegram_chat_id);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_weeks ON weeks(season_year, week_number);
```

## Next Steps (Step 2)

- Convert all broadcast send flows to async and use `asyncio.gather`.
- Refactor `/sendweek`, `/getscores`, `/seasonboard` into this module set.
- Add admin confirmation flow for `/sendweek` and ambiguous names.
- Add `/gamedetails`, tie-breaker rules display, and richer logging granularity.

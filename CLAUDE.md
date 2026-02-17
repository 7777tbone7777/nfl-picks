# NFL Picks Bot

Telegram bot for managing NFL picks competitions with ATS (against the spread) scoring.

## Stack
- **Bot**: python-telegram-bot v22.5
- **Web**: Flask + Flask-SQLAlchemy
- **DB**: PostgreSQL (Heroku)
- **Scheduler**: APScheduler
- **Deployment**: Heroku

## Entry Points
- `bot/bot_runner.py` - Telegram bot polling entry point
- `wsgi.py` - Gunicorn/Flask entry for web endpoints
- `Procfile` - Heroku process definitions

## Database Models (`models.py`)
| Model | Purpose |
|-------|---------|
| `Week` | NFL week with picks_deadline, season_year |
| `Game` | Games with espn_game_id, scores, favorite_team, spread_pts |
| `Participant` | Users linked via telegram_chat_id |
| `Pick` | User's selected_team per game |
| `PropBet` | Prop bets (OVER/UNDER, YES/NO) per week |
| `PropPick` | User's prop selections |
| `Reminder` | Tracks sent notifications |

## Key Files
| File | Purpose |
|------|---------|
| `bot/jobs.py` | Core logic: ESPN integration, game sending, ATS scoring, odds import |
| `bot/telegram_handlers.py` | All command handlers including admin commands |
| `grade_props_auto.py` | Auto-grader for props using ESPN player stats |
| `espn_client.py` | ESPN API client for scores/schedules |

## Telegram Commands

### User Commands
- `/start` - Link Telegram account to participant
- `/mypicks` - View your picks for current season
- `/myprops` - View your prop bet picks
- `/seasonboard [all]` - Season-to-date ATS scoreboard

### Admin Commands (`/admin <subcommand>`)
**Game Management:**
- `sendweek <week> [dry|me|name]` - Send unpicked games
- `syncscores` - Sync scores from ESPN
- `gameids <week>` - List game IDs for a week
- `setspread <game_id> <team> <pts|clear>` - Set/clear spreads
- `import upcoming` - Import upcoming week from ESPN
- `winners` - Announce weekly winners

**Participant Management:**
- `participants` - List all participants
- `remove <id|name>` - Remove participant and their picks
- `deletepicks <name> <week> [season] [dry]` - Delete picks

**Prop Bets:**
- `sendprops <week>` - Send prop bets to all participants
- `listprops <week>` - List props with status
- `gradeprop <prop_id> <result>` - Grade single prop
- `gradeallprops <week> <result1,result2,...>` - Bulk grade
- `propscores <week>` - View prop scores
- `sendpropscores <week>` - Broadcast prop scores
- `shareprops <week>` - Share everyone's prop picks
- `whoisleftprops <week>` - Who hasn't completed props
- `clearprops <week>` - Delete all props for a week

**ATS Scoring:**
- `winnersats <week> [season] [debug]` - Calculate ATS winners

## External APIs
- **ESPN**: Scores, schedules, player stats (`espn_client.py`)
- **The Odds API**: Spread lines (`import_odds_upcoming()` in jobs.py)

## Environment Variables
```
DATABASE_URL          # Heroku Postgres
TELEGRAM_BOT_TOKEN    # Telegram bot token
ADMIN_USER_IDS        # Comma-separated admin Telegram IDs
ODDS_API_KEY          # The Odds API key (optional)
ALLOW_ANYDAY          # Override Tuesday-only odds import
```

## Heroku
- **App name**: `nfl-picks-2025`
- **URL**: `https://nfl-picks-2025-c5f1fa68f866.herokuapp.com/`
- Run scripts: `heroku run python <script.py> -a nfl-picks-2025`

## MCP Access
`nfl-picks-db` MCP server provides direct PostgreSQL access via dbhub.

## Playoff Week Numbering
Internal week numbers map to ESPN playoff weeks:
| Internal Week | ESPN Week | Round |
|---------------|-----------|-------|
| 19 | Playoff 1 | Wild Card |
| 20 | Playoff 2 | Divisional |
| 21 | Playoff 3 | Conference Championship |
| 22 | Playoff 4 | Pro Bowl |
| 23 | Playoff 5 | Super Bowl |

**Known Issue**: ESPN import may create placeholder games with "NFC @ AFC" team names during playoffs instead of actual team names. This happens when the ESPN API returns conference placeholders before matchups are set.

**Fix**: Use `fix_superbowl.py` as a template to manually update game teams/spread and send corrected picks. See script for example of bulk prop creation.

## Common Tasks

### Send week's games to participants
```
/admin sendweek <week> dry    # Preview
/admin sendweek <week>        # Send to all
/admin sendweek <week> me     # Test on yourself
```

### Grade props after games
```
/admin listprops <week>                           # See prop IDs
/admin gradeallprops <week> OVER,UNDER,YES,...   # Bulk grade
/admin sendpropscores <week>                      # Broadcast results
```

### Check season standings
```
/seasonboard        # View only
/seasonboard all    # Broadcast to everyone
```

### Fix playoff/Super Bowl games manually
If ESPN imports placeholder teams ("NFC @ AFC"), use a one-off script:
```bash
heroku run python fix_superbowl.py -a nfl-picks-2025
```
See `fix_superbowl.py` for example of:
- Updating game with correct teams and spread
- Creating multiple prop bets
- Sending game + props to all participants

## Offseason Mode
The `OFFSEASON` env var on Heroku disables all 4 Scheduler jobs (import-week-upcoming, announce-winners, sendweek_upcoming, import-odds-upcoming) without deleting them. Jobs still run on schedule but exit immediately.

- **Enable (after season ends):** `heroku config:set OFFSEASON=true -a nfl-picks-2025`
- **Disable (before Week 1 in September):** `heroku config:remove OFFSEASON -a nfl-picks-2025`

## One-Off Scripts
| Script | Purpose |
|--------|---------|
| `fix_superbowl.py` | Fix Super Bowl game teams/spread, create props, send to participants |
| `verify_scores.py` | Verify/debug score calculations |
| `import_props.py` | Import props from CSV |
| `grade_props_auto.py` | Auto-grade props using ESPN stats |

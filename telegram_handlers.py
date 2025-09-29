
import logging
from sqlalchemy import text as _text
from models import db, Participant, Game, Week, Pick
from .time_utils import now_utc, to_naive_utc

log = logging.getLogger("telegram_handlers")

async def start(update, context):
    chat = update.effective_chat
    user = update.effective_user
    chat_id = chat.id

    p = Participant.query.filter_by(telegram_chat_id=chat_id).first()
    if p:
        await update.message.reply_text(f"👋 You're already registered as {p.name}.")
        return

    base = user.full_name or user.username or user.first_name or f"user_{chat_id}"
    name = base
    suffix = 1
    while Participant.query.filter_by(name=name).first():
        suffix += 1
        name = f"{base} ({suffix})"

    p = Participant(name=name, telegram_chat_id=chat_id)
    db.session.add(p)
    db.session.commit()

    await update.message.reply_text(f"✅ Registered as {p.name}. Use /sendweek to get your picks.")

async def handle_pick(update, context):
    query = update.callback_query
    await query.answer()

    try:
        _, game_id, team = (query.data or "").split(":", 3)[:3]
        game_id = int(game_id)
    except Exception:
        await query.edit_message_text("❌ Invalid pick payload.")
        return

    game = Game.query.get(game_id)
    if not game:
        await query.edit_message_text("❌ Game not found.")
        return

    now_naive = to_naive_utc(now_utc())
    if game.game_time and now_naive >= game.game_time:
        await query.edit_message_text("⛔ Picks closed. That game has started.")
        return

    chat_id = query.message.chat_id
    participant = Participant.query.filter_by(telegram_chat_id=chat_id).first()
    if not participant:
        await query.edit_message_text("❌ Please /start first to register.")
        return

    existing = Pick.query.filter_by(participant_id=participant.id, game_id=game.id).first()
    if existing:
        existing.selected_team = team
    else:
        existing = Pick(participant_id=participant.id, game_id=game.id, selected_team=team)
        db.session.add(existing)

    db.session.commit()

    kickoff_str = (game.game_time.isoformat(' ') + ' UTC') if game.game_time else 'TBD'
    await query.edit_message_text(
        f"✅ Your pick for {game.away_team} @ {game.home_team} is <b>{team}</b>.\nKickoff: {kickoff_str}",
        parse_mode="HTML"
    )

async def mypicks(update, context):
    args = (context.args or [])
    week = None
    if args:
        try:
            week = int(args[0])
        except:
            pass

    if week is None:
        row = db.session.execute(_text('''
            SELECT w.week_number
            FROM weeks w
            JOIN games g ON g.week_id=w.id
            GROUP BY w.week_number, w.season_year
            HAVING MIN(g.game_time) > NOW() AT TIME ZONE 'UTC'
            ORDER BY w.season_year DESC, w.week_number ASC
            LIMIT 1
        ''')).first()
        if row:
            week = int(row[0])
        else:
            row = db.session.execute(_text('SELECT MAX(week_number) FROM weeks')).first()
            week = int(row[0] or 1)

    chat_id = update.effective_chat.id
    p = Participant.query.filter_by(telegram_chat_id=chat_id).first()
    if not p:
        await update.message.reply_text("❌ Please /start first to register.")
        return

    rows = db.session.execute(_text('''
        SELECT g.id, g.away_team, g.home_team, g.game_time,
               (SELECT selected_team FROM picks WHERE participant_id=:pid AND game_id=g.id) AS selected_team
        FROM games g
        JOIN weeks w ON w.id=g.week_id
        WHERE w.week_number=:w
        ORDER BY g.game_time NULLS LAST, g.id
    '''), {"pid": p.id, "w": week}).mappings().all()

    if not rows:
        await update.message.reply_text(f"No games found for Week {week}.")
        return

    lines = [f"<b>📝 Your picks — Week {week}</b>"]
    for r in rows:
        sel = r["selected_team"] or "—"
        when = r["game_time"].isoformat(" ") + " UTC" if r["game_time"] else "TBD"
        lines.append(f"{r['away_team']} @ {r['home_team']} — <b>{sel}</b>  ({when})")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

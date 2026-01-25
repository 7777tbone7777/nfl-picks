# bot/telegram_handlers.py

from __future__ import annotations
# add these

from bot.jobs import create_app, db, _send_message, _pt, _spread_label, send_week_games
from sqlalchemy import text as T


import os
import json
import asyncio
import logging
from typing import Any, Dict, List, Optional

from telegram import Update
from telegram.ext import ContextTypes

# Re-export command handlers that live in jobs.py
from bot.jobs import (
    handle_pick,
    handle_prop_pick,
    start,
    sendweek_command,
    syncscores_command,
    getscores_command,
    deletepicks_command,
    whoisleft_command,
    seepicks_command,
    remindweek_command,
    # Prop bet functions
    send_props,
    import_props_from_csv,
    grade_prop,
    prop_scores,
    clear_props,
    list_props,
)

# DB access for /mypicks
from models import db  # type: ignore
from sqlalchemy import text

log = logging.getLogger(__name__)

# --- /admin command with subcommands ----------------------------------------

# ADMIN_IDS already used elsewhere in your app; re-derive here from env
ADMIN_IDS = {int(x) for x in (os.getenv("ADMIN_IDS") or "").split(",") if x.strip().isdigit()}

# --- /seasonboard (finals-only) ---
async def seasonboard_command(update, context):
    """
    Shows season-to-date scoreboard for weeks that have at least one FINAL game.
    Usage:
        /seasonboard           - Show scoreboard (to you only)
        /seasonboard me        - Same as above
        /seasonboard all       - Broadcast scoreboard to all participants
        /seasonboard <year>    - Show specific season year
    """
    from sqlalchemy import text as T
    from bot.jobs import create_app, db, _send_message
    user = update.effective_user

    args = (context.args or [])
    season_year = None
    broadcast_all = False

    for a in args:
        if a.isdigit():
            season_year = int(a)
        elif a.lower() == "all":
            broadcast_all = True

    app = create_app()
    with app.app_context():
        # Resolve season if not provided
        if season_year is None:
            season_year = db.session.execute(T("SELECT MAX(season_year) FROM weeks")).scalar()

        # 1) Figure out which week_numbers actually have at least one FINAL game
        weeks = [
            r["week_number"]
            for r in db.session.execute(
                T("""
                SELECT DISTINCT w.week_number
                  FROM weeks w
                  JOIN games g ON g.week_id = w.id
                 WHERE w.season_year = :y
                   AND LOWER(COALESCE(g.status,'')) = 'final'
                 ORDER BY w.week_number
                """),
                {"y": season_year},
            ).mappings()
        ]

        if not weeks:
            await update.message.reply_text(f"No FINAL games yet for {season_year}.")
            return

        # 2) Load participant names and chat IDs
        participants = db.session.execute(
            T("SELECT id, name, telegram_chat_id FROM participants")
        ).mappings().all()
        names = {p["id"]: p["name"] for p in participants}

        # 3) Pull all FINAL picks for those weeks (using ATS winner from DB)
        rows = db.session.execute(
            T("""
              SELECT p.participant_id  AS pid,
                     w.week_number     AS wk,
                     g.winner          AS ats_winner,
                     p.selected_team   AS pick
                FROM picks p
                JOIN games g  ON g.id = p.game_id
                JOIN weeks w  ON w.id = g.week_id
               WHERE w.season_year = :y
                 AND w.week_number IN :weeks
                 AND LOWER(COALESCE(g.status,'')) = 'final'
                 AND p.selected_team IS NOT NULL
            """).bindparams(weeks=tuple(weeks)),
            {"y": season_year},
        ).mappings().all()

        # 4) Compute wins per participant per week (using ATS winner from DB)
        wins_by_pid = {}          # pid -> total wins
        wins_by_pid_week = {}     # pid -> {wk -> wins}
        for r in rows:
            ats_winner = r["ats_winner"]
            if not ats_winner:  # NULL means push - no winner
                continue
            if r["pick"] and r["pick"].strip().lower() == ats_winner.strip().lower():
                pid = int(r["pid"]); wk = int(r["wk"])
                wins_by_pid[pid] = wins_by_pid.get(pid, 0) + 1
                wk_map = wins_by_pid_week.setdefault(pid, {})
                wk_map[wk] = wk_map.get(wk, 0) + 1

        # Ensure every participant shows even if zero
        for pid in names:
            wins_by_pid.setdefault(pid, 0)
            wins_by_pid_week.setdefault(pid, {})

        # 5) Render a compact board
        header = "🏆 Season-to-date Scoreboard\n"
        sub = f"Season {season_year} — completed games only"
        week_cols = " ".join([f"W{w:>2}" if w >= 10 else f"W{w}" for w in weeks])

        lines = []
        # Sort by total desc, then name asc for stability
        for pid, total in sorted(wins_by_pid.items(), key=lambda kv: (-kv[1], names.get(kv[0], ""))):
            per_week = [str(wins_by_pid_week[pid].get(w, 0)) for w in weeks]
            lines.append(f"{names.get(pid, pid):<12} | {' '.join(per_week)} | Total {total}")

        body = "\n".join(lines)
        msg = f"{header}{sub}\n\nName         | {week_cols} | Total\n{body}"

        # 6) Send to all participants or just reply
        if broadcast_all:
            sent_count = 0
            for p in participants:
                chat_id = p.get("telegram_chat_id")
                if chat_id:
                    try:
                        _send_message(chat_id, msg)
                        sent_count += 1
                    except Exception as e:
                        log.warning(f"Failed to send scoreboard to {p['name']}: {e}")
            await update.message.reply_text(f"✅ Scoreboard sent to {sent_count} participant(s).")
        else:
            await update.message.reply_text(msg)


def _is_admin(user) -> bool:
    try:
        return bool(ADMIN_IDS) and user and (user.id in ADMIN_IDS)
    except Exception:
        return False

def _parse_admin_args(text: str):
    # "/admin <subcommand> [args...]"
    parts = (text or "").strip().split()
    sub = parts[1].lower() if len(parts) >= 2 else ""
    rest = parts[2:] if len(parts) >= 3 else []
    return sub, rest

async def sendweek_command(update, context):
    """
    Usage:
      /sendweek <week>            -> send to ALL
      /sendweek <week> dry        -> dry-run (counts only)
      /sendweek <week> me         -> send ONLY to the caller
      /sendweek <week> <name...>  -> send ONLY to that participant by name
    """
    user = update.effective_user
    chat = update.effective_chat
    args = context.args or []

    # ---- Admin gate (keep your behavior) ----
    try:
        from bot.config import ADMIN_IDS  # if you keep ADMIN_IDS in config
    except Exception:
        ADMIN_IDS = set()
    if ADMIN_IDS and (not user or user.id not in ADMIN_IDS):
        if update.message:
            await update.message.reply_text("Sorry, admin only.")
        return

    # ---- Parse args ----
    if not args or not args[0].isdigit():
        if update.message:
            await update.message.reply_text(
                "Usage: /sendweek <week_number> [dry|me|<participant name>]"
            )
        return
    week_number = int(args[0])
    target = "all" if len(args) == 1 else " ".join(args[1:]).strip()

    def _build_text(g):
        # g['game_time'] is a naive/UTC dt in DB; _pt will handle to local label you've set up
        when = _pt(g.get("game_time"))
        line1 = f"{g['away_team']} @ {g['home_team']}"
        line2 = when
        line3 = _spread_label(g)
        return f"{line1}\n{line2}\n{line3}"

    def _kb_for(g):
        return {
            "inline_keyboard": [
                [{"text": g["away_team"], "callback_data": f"pick:{g['id']}:{g['away_team']}"}],
                [{"text": g["home_team"], "callback_data": f"pick:{g['id']}:{g['home_team']}"}],
            ]
        }

    # ---- Core sending logic (SQL queries) ----
    app = create_app()
    with app.app_context():
        # Find an existing week (latest season if multiple)
        wk = db.session.execute(
            T("""
              SELECT id, season_year
                FROM weeks
               WHERE week_number=:w
               ORDER BY season_year DESC
               LIMIT 1
            """),
            {"w": week_number},
        ).mappings().first()

        if not wk:
            if update.message:
                await update.message.reply_text(f"Week {week_number} not found.")
            return

        season_year = int(wk["season_year"])

        # Fetch games for the week (for each person we’ll filter out picked ones)
        base_games_sql = T("""
            SELECT g.id, g.away_team, g.home_team, g.game_time,
                   g.favorite_team AS favorite_team, g.spread_pts AS spread_pts
              FROM games g
              JOIN weeks w ON w.id = g.week_id
             WHERE w.season_year = :y AND w.week_number = :w
             ORDER BY g.game_time NULLS LAST, g.id
        """)

        # Helper: send unpicked games to one participant id/chat

        def _send_to_one(participant_id: int, chat_id: str) -> int:
            rows = db.session.execute(
                T("""
                    SELECT
                           g.id,
                           g.away_team,
                           g.home_team,
                           g.game_time,
                           g.favorite_team AS favorite_team,   -- ensure key exists
                           g.spread_pts     AS spread_pts      -- ensure key exists
                      FROM games g
                      JOIN weeks w
                        ON w.id = g.week_id
                      LEFT JOIN picks p
                        ON p.game_id = g.id
                       AND p.participant_id = :pid
                     WHERE w.season_year = :y
                       AND w.week_number = :w
                       AND (p.id IS NULL OR p.selected_team IS NULL)
                     ORDER BY g.game_time NULLS LAST, g.id
                  """),
                  {"pid": participant_id, "y": season_year, "w": week_number},
              ).mappings().all()  # <-- keep this

            sent = 0
            for g in rows:
                text = _build_text(g)
                kb = _kb_for(g)
                try:
                    _send_message(str(chat_id), text, reply_markup=kb)
                    sent += 1
                except Exception as e:
                    # Don’t crash the whole command on one bad chat
                    print(f"[sendweek] failed to send to {chat_id}: {e}")
            return sent

        # --- Target: DRY RUN ---
        if target.lower() == "dry":
            # For each registered participant, count how many messages would be sent
            people = db.session.execute(
                T("""
                   SELECT id, name, telegram_chat_id
                     FROM participants
                    WHERE telegram_chat_id IS NOT NULL
                """)
            ).mappings().all()
            total_msgs = 0
            for u in people:
                cnt = db.session.execute(
                    T("""
                        SELECT COUNT(*)
                          FROM games g
                          JOIN weeks w ON w.id = g.week_id
                     LEFT JOIN picks p ON p.game_id = g.id AND p.participant_id = :pid
                         WHERE w.season_year = :y AND w.week_number = :w
                           AND (p.id IS NULL OR p.selected_team IS NULL)
                    """),
                    {"pid": u["id"], "y": season_year, "w": week_number},
                ).scalar()
                total_msgs += int(cnt or 0)
            await update.message.reply_text(
                f"DRY RUN: would send {total_msgs} button message(s) to {len(people)} participant(s) "
                f"for Week {week_number} ({season_year})."
            )
            return

        # --- Target: ME ---
        if target.lower() == "me":
            me_chat = str(chat.id)
            me = db.session.execute(
                T("SELECT id, telegram_chat_id FROM participants WHERE telegram_chat_id = :c"),
                {"c": me_chat},
            ).mappings().first()
            if not me:
                await update.message.reply_text("You're not linked yet. Send /start first.")
                return
            sent = _send_to_one(me["id"], me["telegram_chat_id"])
            await update.message.reply_text(f"✅ Sent {sent} unpicked game(s) for Week {week_number} to you.")
            return

        # --- Target: specific name ---
        if target.lower() not in ("all",):
            person = db.session.execute(
                T("""
                  SELECT id, name, telegram_chat_id
                    FROM participants
                   WHERE LOWER(name) = LOWER(:n)
                """),
                {"n": target},
            ).mappings().first()
            if not person:
                await update.message.reply_text(f"Participant '{target}' not found.")
                return
            if not person["telegram_chat_id"]:
                await update.message.reply_text(
                    f"Participant '{person['name']}' has no Telegram chat linked. Ask them to /start."
                )
                return
            sent = _send_to_one(person["id"], person["telegram_chat_id"])
            await update.message.reply_text(f"✅ Sent {sent} unpicked game(s) to {person['name']}.")
            return

        # --- Target: ALL ---
        if update.message:
            await update.message.reply_text(f"Sending Week {week_number} to all registered participants…")

        people = db.session.execute(
            T("SELECT id, name, telegram_chat_id FROM participants WHERE telegram_chat_id IS NOT NULL")
        ).mappings().all()

        total = 0
        for u in people:
            total += _send_to_one(u["id"], u["telegram_chat_id"])

        if update.message:
            await update.message.reply_text(f"✅ Done. Sent {total} unpicked game(s) to {len(people)} participant(s).")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /admin participants
    /admin remove <id|name...>
    /admin deletepicks <id|name...> <week> [season_year] [dry]
    /admin gameids <week> [season_year]
    /admin setspread <game_id> <favorite_team> <points|clear>
    /admin sendweek upcoming
    /admin import upcoming
    /admin winners
    """
    user = update.effective_user
    # ---- admin check ----
    if not _is_admin(user):
        if update.message:
            await update.message.reply_text("Sorry, admin only.")
        return

    if not update.message or not update.message.text:
        await update.message.reply_text(
            "Usage: /admin <participants|remove|deletepicks|gameids|setspread|sendweek upcoming|import upcoming|winners>"
        )
        return

    # ---- parse ----
    parts = update.message.text.strip().split()
    sub = parts[1].lower() if len(parts) >= 2 else ""
    rest = parts[2:] if len(parts) >= 3 else []

    # ---- participants ----
    if sub == "participants":
        from bot.jobs import create_app, db
        from sqlalchemy import text as T
        app = create_app()
        with app.app_context():
            rows = db.session.execute(
                T("SELECT id, name, COALESCE(telegram_chat_id,'') AS chat FROM participants ORDER BY id")
            ).mappings().all()
        lines = [f"{r['id']:>4} | {r['name']} | chat={r['chat']}" for r in rows]
        await update.message.reply_text("Participants:\n" + ("\n".join(lines) if lines else "(none)"))
        return

    # ---- remove ----
    if sub == "remove":
        if not rest:
            await update.message.reply_text("Usage: /admin remove <id|name...>")
            return
        target = " ".join(rest).strip()
        from bot.jobs import create_app, db
        from sqlalchemy import text as T
        app = create_app()
        with app.app_context():
            if target.isdigit():
                pid = int(target)
                row = db.session.execute(T("SELECT id,name FROM participants WHERE id=:pid"), {"pid": pid}).mappings().first()
                if not row:
                    await update.message.reply_text(f"ID {pid} not found.")
                    return
                db.session.execute(T("DELETE FROM picks WHERE participant_id=:pid"), {"pid": pid})
                db.session.execute(T("DELETE FROM participants WHERE id=:pid"), {"pid": pid})
                db.session.commit()
                await update.message.reply_text(f"Deleted {row['name']} (id={pid}) and their picks.")
            else:
                row = db.session.execute(
                    T("SELECT id,name FROM participants WHERE lower(name)=lower(:n)"),
                    {"n": target}
                ).mappings().first()
                if not row:
                    await update.message.reply_text(f"Participant '{target}' not found.")
                    return
                pid = int(row["id"])
                db.session.execute(T("DELETE FROM picks WHERE participant_id=:pid"), {"pid": pid})
                db.session.execute(T("DELETE FROM participants WHERE id=:pid"), {"pid": pid})
                db.session.commit()
                await update.message.reply_text(f"Deleted {row['name']} (id={pid}) and their picks.")
        return

    # ---- deletepicks ----
    if sub == "deletepicks":
        if len(rest) < 2:
            await update.message.reply_text(
                "Usage: /admin deletepicks <id|name...> <week_number> [season_year] [dry]"
            )
            return

        # identify numeric tokens at end
        nums = [x for x in rest if x.isdigit()]
        dry = any(x.lower() == "dry" for x in rest)
        if not nums:
            await update.message.reply_text(
                "Missing week number. Usage: /admin deletepicks <id|name...> <week_number> [season_year] [dry]"
            )
            return
        week_number = int(nums[0])
        season_year = int(nums[1]) if len(nums) >= 2 else None

        # target is everything before those numbers/flags
        cut = rest.index(nums[0])
        target_name_or_id = " ".join(rest[:cut]).strip() or rest[0]

        from bot.jobs import create_app, db
        from sqlalchemy import text as T
        app = create_app()
        with app.app_context():
            # resolve participant
            if target_name_or_id.isdigit():
                pid = db.session.execute(
                    T("SELECT id FROM participants WHERE id=:pid"), {"pid": int(target_name_or_id)}
                ).scalar()
                pname = db.session.execute(
                    T("SELECT name FROM participants WHERE id=:pid"), {"pid": int(target_name_or_id)}
                ).scalar()
            else:
                row = db.session.execute(
                    T("SELECT id,name FROM participants WHERE lower(name)=lower(:n)"),
                    {"n": target_name_or_id},
                ).mappings().first()
                pid = row["id"] if row else None
                pname = row["name"] if row else None

            if not pid:
                await update.message.reply_text(f"Participant '{target_name_or_id}' not found.")
                return

            if season_year is None:
                season_year = db.session.execute(T("SELECT MAX(season_year) FROM weeks")).scalar()

            cnt = db.session.execute(
                T("""
                   SELECT COUNT(*) FROM picks p
                   JOIN games g ON g.id = p.game_id
                   JOIN weeks w ON w.id = g.week_id
                   WHERE p.participant_id = :pid
                     AND w.season_year = :y
                     AND w.week_number = :w
                """),
                {"pid": int(pid), "y": int(season_year), "w": int(week_number)},
            ).scalar() or 0

            if dry:
                await update.message.reply_text(
                    f"[DRY RUN] {pname or pid}: would delete {cnt} pick(s) for Week {week_number} ({season_year})."
                )
                return

            db.session.execute(
                T("""
                   DELETE FROM picks
                   USING games g, weeks w
                   WHERE picks.game_id = g.id
                     AND w.id = g.week_id
                     AND picks.participant_id = :pid
                     AND w.season_year = :y
                     AND w.week_number = :w
                """),
                {"pid": int(pid), "y": int(season_year), "w": int(week_number)},
            )
            db.session.commit()

        await update.message.reply_text(
            f"Deleted {cnt} pick(s) for {pname or pid} in Week {week_number} ({season_year})."
        )
        return

    if sub in {"winnersats", "winners-ats"}:
        # Usage: /admin winnersats <week_number> [season_year] [debug]
        if not rest or not rest[0].isdigit():
            await update.message.reply_text(
                "Usage: /admin winnersats <week_number> [season_year] [debug]"
            ) 
            return

        week_number = int(rest[0])
        season_year = None
        debug_mode = False

        # optional season_year
        if len(rest) >= 2 and rest[1].isdigit():
            season_year = int(rest[1])

        # optional "debug"
        if len(rest) >= 3 and rest[2].lower() == "debug":
            debug_mode = True
        elif len(rest) >= 2 and rest[1].lower() == "debug":
            debug_mode = True

        from bot.jobs import create_app, db, _ats_winner
        from sqlalchemy import text as T

        app = create_app()
        with app.app_context():
            if season_year is None:
                season_year = db.session.execute(T("SELECT MAX(season_year) FROM weeks")).scalar()

            # Pull FINAL games for the week
            games = db.session.execute(
                T("""
                  SELECT g.id, g.home_team, g.away_team, g.home_score, g.away_score,
                         g.favorite_team, g.spread_pts
                  FROM games g
                  JOIN weeks w ON w.id = g.week_id
                  WHERE w.season_year = :y AND w.week_number = :w AND lower(g.status) = 'final'
                  ORDER BY g.id
                """),
                {"y": season_year, "w": week_number},
            ).mappings().all()

            if not games:
                await update.message.reply_text(
                    f"No FINAL games for Week {week_number} ({season_year})."
                )
                return

            # Compute winners per game id using hybrid logic (None == push/unknown)
            winners = {}
            for g in games:
                winners[int(g["id"])] = _ats_winner(
                    g["home_team"], g["away_team"],
                    g["home_score"], g["away_score"],
                    g["favorite_team"], g["spread_pts"],
                    week_number=week_number,
                    season_year=season_year,  # Pass both for hybrid logic
                )  

            # All picks with selections for the week
            picks = db.session.execute(
                T("""
                   SELECT p.participant_id, p.selected_team, p.game_id
                   FROM picks p
                   JOIN games g ON g.id = p.game_id
                   JOIN weeks w ON w.id = g.week_id
                   WHERE w.season_year = :y AND w.week_number = :w
                      AND p.selected_team IS NOT NULL
                """),
                {"y": season_year, "w": week_number},
            ).mappings().all()

            # Name lookup
            names = dict(db.session.execute(T("SELECT id, name FROM participants")).fetchall())

        # Tally wins (case-insensitive, ignore pushes)
        score = {}
        detail_lines = []
        for p in picks:
            gid = int(p["game_id"])
            wt = winners.get(gid)
            sel = (p["selected_team"] or "").strip()
            if not wt:
                # push or unknown—skip
                continue
            if sel.lower() == wt.strip().lower():
                score[p["participant_id"]] = score.get(p["participant_id"], 0) + 1
                if debug_mode:
                    detail_lines.append(f"+ {names.get(p['participant_id'], p['participant_id'])} ✓ ({sel}) on g{gid} [{wt}]")
            else:
                if debug_mode:
                    detail_lines.append(f"- {names.get(p['participant_id'], p['participant_id'])} × ({sel}) on g{gid} [ATS={wt}]")

        if not score:
            await update.message.reply_text(
                f"No ATS wins computed for Week {week_number} ({season_year})."
            )
            return

        # Pretty summary (sort by wins desc, then name)
        lines = [
            f"{names.get(pid, pid)} — {wins}"
            for pid, wins in sorted(score.items(), key=lambda x: (-x[1], names.get(x[0], "")))
        ]
        msg = f"ATS winners (dry run) — Week {week_number} ({season_year}):\n" + "\n".join(lines)
        if debug_mode and detail_lines:
            msg += "\n\nDetails:\n" + "\n".join(detail_lines)
        await update.message.reply_text(msg)
        return

    # ---- gameids ----
    if sub == "gameids":
        if not rest or not rest[0].isdigit():
            await update.message.reply_text("Usage: /admin gameids <week_number> [season_year]")
            return
        week_number = int(rest[0])
        season_year = int(rest[1]) if len(rest) >= 2 and rest[1].isdigit() else None

        from bot.jobs import create_app, db
        from sqlalchemy import text as T
        app = create_app()
        with app.app_context():
            if season_year is None:
                season_year = db.session.execute(T("SELECT MAX(season_year) FROM weeks")).scalar()
            rows = db.session.execute(
                T("""
                   SELECT g.id, g.away_team, g.home_team, g.game_time,
                          g.favorite_team, g.spread_pts
                   FROM games g
                   JOIN weeks w ON w.id = g.week_id
                   WHERE w.season_year = :y AND w.week_number = :w
                   ORDER BY g.game_time NULLS LAST, g.id
                """),
                {"y": int(season_year), "w": int(week_number)},
            ).mappings().all()

        if not rows:
            await update.message.reply_text(f"No games for Week {week_number} ({season_year}).")
            return

        def fmt(r):
            odds = ""
            if r["favorite_team"] and r["spread_pts"] is not None:
                odds = f"  ({r['favorite_team']} -{float(r['spread_pts']):g})"
            kt = r["game_time"].isoformat(" ", "minutes") if r["game_time"] else "TBD"
            return f"{r['id']:>4} | {r['away_team']} @ {r['home_team']}{odds} | {kt}"

        await update.message.reply_text(
            f"Week {week_number} ({season_year}) game IDs:\n" + "\n".join(fmt(r) for r in rows)
        )
        return

    # ---- setspread ----
    if sub == "setspread":
        # /admin setspread <game_id> <favorite_team> <points|clear>
        if len(rest) < 2:
            await update.message.reply_text("Usage: /admin setspread <game_id> <favorite_team> <points|clear>")
            return

        gid_text = rest[0]
        if not gid_text.isdigit():
            await update.message.reply_text("game_id must be an integer.")
            return
        gid = int(gid_text)

        # everything after game_id: last token is points|clear, the rest is the favorite name (can be multi-word)
        if len(rest) < 3:
            await update.message.reply_text("Usage: /admin setspread <game_id> <favorite_team> <points|clear>")
            return

        pts_raw = rest[-1].lower()
        fav = " ".join(rest[1:-1]).strip()
        if not fav:
            await update.message.reply_text("Favorite team name is required.")
            return

        from bot.jobs import create_app, db
        from sqlalchemy import text as T
        app = create_app()
        with app.app_context():
            if pts_raw == "clear":
                db.session.execute(T("UPDATE games SET favorite_team=NULL, spread_pts=NULL WHERE id=:gid"), {"gid": gid})
                db.session.commit()
                await update.message.reply_text(f"Cleared odds for game {gid}.")
                return
            try:
                pts = float(pts_raw)
            except Exception:
                await update.message.reply_text("Spread must be a number or 'clear'.")
                return

            db.session.execute(
                T("UPDATE games SET favorite_team=:fv, spread_pts=:sp WHERE id=:gid"),
                {"fv": fav, "sp": pts, "gid": gid},
            )
            db.session.commit()
            await update.message.reply_text(f"Set game {gid} odds: favorite={fav}, spread={pts:g}")
        return

    # ---- send/import/winners ----
    if sub == "sendweek" and rest[:1] == ["upcoming"]:
        from importlib import import_module
        jobs = import_module("bot.jobs")
        res = jobs.cron_send_upcoming_week()
        await update.message.reply_text("sendweek_upcoming:\n" + json.dumps(res, default=str, indent=2))
        return

    if sub == "import" and rest[:1] == ["upcoming"]:
        from importlib import import_module
        jobs = import_module("bot.jobs")
        res = jobs.cron_import_upcoming_week()
        await update.message.reply_text("import-week-upcoming:\n" + json.dumps(res, default=str, indent=2))
        return

    if sub == "winners":
        from importlib import import_module
        jobs = import_module("bot.jobs")
        res = jobs.cron_announce_weekly_winners()
        await update.message.reply_text("announce-winners:\n" + json.dumps(res, default=str, indent=2))
        return

    # ---- PROP BETS ----

    # /admin sendprops <week>
    if sub == "sendprops":
        if not rest or not rest[0].isdigit():
            await update.message.reply_text("Usage: /admin sendprops <week_number> [season_year]")
            return
        week = int(rest[0])
        season_year = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else None
        res = send_props(week, season_year)
        await update.message.reply_text(f"sendprops:\n{json.dumps(res, default=str, indent=2)}")
        return

    # /admin listprops <week>
    if sub == "listprops":
        if not rest or not rest[0].isdigit():
            await update.message.reply_text("Usage: /admin listprops <week_number> [season_year]")
            return
        week = int(rest[0])
        season_year = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else None
        res = list_props(week, season_year)
        if res.get("ok") and res.get("props"):
            lines = []
            for p in res["props"]:
                status = f"✅ {p['result']}" if p["result"] else ("📤" if p["sent"] else "📝")
                lines.append(f"{p['id']:>3} | {p['game_label'] or '':<3} | {status} | {p['description'][:40]}")
            await update.message.reply_text(
                f"Props for Week {week}:\n" + "\n".join(lines)
            )
        else:
            await update.message.reply_text(f"listprops:\n{json.dumps(res, default=str, indent=2)}")
        return

    # /admin gradeprop <prop_id> <result>
    if sub == "gradeprop":
        if len(rest) < 2:
            await update.message.reply_text("Usage: /admin gradeprop <prop_id> <result>")
            return
        try:
            prop_id = int(rest[0])
        except ValueError:
            await update.message.reply_text("prop_id must be an integer.")
            return
        result = rest[1]
        res = grade_prop(prop_id, result)
        await update.message.reply_text(f"gradeprop:\n{json.dumps(res, default=str, indent=2)}")
        return

    # /admin propscores <week>
    if sub == "propscores":
        if not rest or not rest[0].isdigit():
            await update.message.reply_text("Usage: /admin propscores <week_number> [season_year]")
            return
        week = int(rest[0])
        season_year = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else None
        res = prop_scores(week, season_year)
        if res.get("ok") and res.get("scores"):
            lines = [f"{name}: {score}" for name, score in res["scores"].items()]
            await update.message.reply_text(
                f"🎯 Prop Scores - Week {week}\n" + "\n".join(lines)
            )
        else:
            await update.message.reply_text(f"propscores:\n{json.dumps(res, default=str, indent=2)}")
        return

    # /admin clearprops <week>
    if sub == "clearprops":
        if not rest or not rest[0].isdigit():
            await update.message.reply_text("Usage: /admin clearprops <week_number> [season_year]")
            return
        week = int(rest[0])
        season_year = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else None
        res = clear_props(week, season_year)
        await update.message.reply_text(f"clearprops:\n{json.dumps(res, default=str, indent=2)}")
        return

    # /admin shareprops <week> - broadcast everyone's picks to all participants
    if sub == "shareprops":
        if not rest or not rest[0].isdigit():
            await update.message.reply_text("Usage: /admin shareprops <week_number> [season_year]")
            return
        week = int(rest[0])
        season_year = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else None

        from bot.jobs import create_app, db
        app = create_app()
        with app.app_context():
            if season_year is None:
                season_year = db.session.execute(T("SELECT MAX(season_year) FROM weeks")).scalar()

            week_id = db.session.execute(
                T("SELECT id FROM weeks WHERE season_year=:y AND week_number=:w"),
                {"y": season_year, "w": week},
            ).scalar()

            if not week_id:
                await update.message.reply_text(f"Week {week} not found.")
                return

            # Get all props for the week
            props = db.session.execute(
                T("""
                    SELECT id, game_label, description, option_a, option_b
                    FROM prop_bets
                    WHERE week_id = :wid
                    ORDER BY game_label, id
                """),
                {"wid": week_id},
            ).mappings().all()

            if not props:
                await update.message.reply_text(f"No props found for Week {week}.")
                return

            # Get all participants
            participants = db.session.execute(
                T("SELECT id, name, telegram_chat_id FROM participants WHERE telegram_chat_id IS NOT NULL")
            ).mappings().all()

            # Get all picks
            picks = db.session.execute(
                T("""
                    SELECT pp.prop_bet_id, pp.participant_id, pp.selected_option, p.name
                    FROM prop_picks pp
                    JOIN participants p ON p.id = pp.participant_id
                    JOIN prop_bets pb ON pb.id = pp.prop_bet_id
                    WHERE pb.week_id = :wid
                """),
                {"wid": week_id},
            ).mappings().all()

            # Build picks lookup: {prop_id: {participant_name: selected_option}}
            picks_by_prop = {}
            for pick in picks:
                prop_id = pick["prop_bet_id"]
                if prop_id not in picks_by_prop:
                    picks_by_prop[prop_id] = {}
                picks_by_prop[prop_id][pick["name"]] = pick["selected_option"]

            # Build message grouped by game (AFC/NFC)
            participant_names = sorted([p["name"] for p in participants])

            afc_lines = ["🏈 AFC PROPS"]
            nfc_lines = ["🏈 NFC PROPS"]

            for prop in props:
                prop_picks = picks_by_prop.get(prop["id"], {})
                # Show who picked what
                pick_summary = []
                for name in participant_names:
                    pick = prop_picks.get(name, "—")
                    pick_summary.append(f"{name}: {pick}")

                line = f"\n{prop['description']}\n" + " | ".join(pick_summary)

                if prop["game_label"] == "AFC":
                    afc_lines.append(line)
                else:
                    nfc_lines.append(line)

            msg = "\n".join(afc_lines) + "\n\n" + "\n".join(nfc_lines)

            # Send to all participants
            sent = 0
            for p in participants:
                try:
                    _send_message(str(p["telegram_chat_id"]), msg)
                    sent += 1
                except Exception as e:
                    log.warning(f"Failed to send prop picks to {p['name']}: {e}")

            await update.message.reply_text(f"✅ Shared prop picks with {sent} participant(s).")
        return

    # /admin whoisleftprops <week> - who hasn't made all their prop picks
    if sub == "whoisleftprops":
        if not rest or not rest[0].isdigit():
            await update.message.reply_text("Usage: /admin whoisleftprops <week_number> [season_year]")
            return
        week = int(rest[0])
        season_year = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else None

        from bot.jobs import create_app, db
        app = create_app()
        with app.app_context():
            if season_year is None:
                season_year = db.session.execute(T("SELECT MAX(season_year) FROM weeks")).scalar()

            week_id = db.session.execute(
                T("SELECT id FROM weeks WHERE season_year=:y AND week_number=:w"),
                {"y": season_year, "w": week},
            ).scalar()

            if not week_id:
                await update.message.reply_text(f"Week {week} not found.")
                return

            # Count total props for the week
            total_props = db.session.execute(
                T("SELECT COUNT(*) FROM prop_bets WHERE week_id = :wid"),
                {"wid": week_id},
            ).scalar() or 0

            if total_props == 0:
                await update.message.reply_text(f"No props found for Week {week}.")
                return

            # Get participants and their pick counts
            rows = db.session.execute(
                T("""
                    SELECT p.id, p.name,
                           COUNT(pp.id) AS picks_made
                    FROM participants p
                    LEFT JOIN prop_picks pp ON pp.participant_id = p.id
                        AND pp.prop_bet_id IN (SELECT id FROM prop_bets WHERE week_id = :wid)
                    WHERE p.telegram_chat_id IS NOT NULL
                    GROUP BY p.id, p.name
                    ORDER BY p.name
                """),
                {"wid": week_id},
            ).mappings().all()

            missing = []
            complete = []
            for r in rows:
                picks_made = r["picks_made"] or 0
                if picks_made < total_props:
                    missing.append(f"{r['name']}: {picks_made}/{total_props}")
                else:
                    complete.append(r["name"])

            lines = [f"🎯 Prop Picks Status - Week {week}\nTotal props: {total_props}\n"]
            if missing:
                lines.append("❌ Incomplete:")
                lines.extend([f"  {m}" for m in missing])
            if complete:
                lines.append("\n✅ Complete:")
                lines.extend([f"  {c}" for c in complete])

            await update.message.reply_text("\n".join(lines))
        return

    # ---- default usage ----
    await update.message.reply_text(
        "Usage: /admin <participants|remove|deletepicks|gameids|setspread|sendweek upcoming|import upcoming|winners|sendprops|listprops|gradeprop|propscores|clearprops|shareprops|whoisleftprops>"
    )

# ---------- helpers for /mypicks ----------

def _format_user_picks(picks: List[Dict[str, Any]]) -> str:
    """Format a user's picks into a readable message."""
    if not picks:
        return "You have no saved picks yet."
    lines = []
    for p in picks:
        week_no = p.get("week_number", "?")
        away = p.get("away_team", "?")
        home = p.get("home_team", "?")
        choice = p.get("selected_team", "?")
        lines.append(f"• Week {week_no} — {away} @ {home} → {choice}")
    return "\n".join(lines)


def _fetch_picks_sync(telegram_user_id: Optional[int]) -> List[Dict[str, Any]]:
    """
    Blocking DB work — executed via asyncio.to_thread() from the async handler.
    Returns dicts with: week_number, away_team, home_team, selected_team.
    """
    if telegram_user_id is None:
        return []

    telegram_chat_id = str(telegram_user_id)

    with db.engine.connect() as conn:  # type: ignore[attr-defined]
        # 1) Find participant by telegram_chat_id
        part_row = conn.execute(
            text(
                """
                SELECT id
                FROM participants
                WHERE telegram_chat_id = :tid
                LIMIT 1
                """
            ),
            {"tid": telegram_chat_id},
        ).fetchone()
        if not part_row:
            return []

        participant_id = part_row[0]

        # 2) Join picks → games → weeks
        rows = conn.execute(
            text(
                """
                SELECT
                  w.week_number,
                  g.away_team,
                  g.home_team,
                  p.selected_team
                FROM picks p
                JOIN games g ON g.id = p.game_id
                JOIN weeks w ON w.id = g.week_id
                WHERE p.participant_id = :pid
                ORDER BY w.season_year DESC, w.week_number ASC, g.game_time ASC
                """
            ),
            {"pid": participant_id},
        ).fetchall()

        picks: List[Dict[str, Any]] = []
        for r in rows:
            picks.append(
                {
                    "week_number": r[0],
                    "away_team": r[1],
                    "home_team": r[2],
                    "selected_team": r[3],
                }
            )
        return picks


async def _load_user_picks(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> List[Dict[str, Any]]:
    """
    Preferred path: use an injected service at application.bot_data['svc'].get_user_picks(user_id).
    Fallback: run the direct DB query in a thread to avoid blocking PTB's event loop.
    """
    user_id = getattr(update.effective_user, "id", None)

    # Service-first (optional)
    svc = None
    if hasattr(context, "application") and hasattr(context.application, "bot_data"):
        svc = context.application.bot_data.get("svc")
    if svc and hasattr(svc, "get_user_picks"):
        result = svc.get_user_picks(user_id)
        return await result if hasattr(result, "__await__") else result

    # Fallback to direct DB, offloaded to a thread
    return await asyncio.to_thread(_fetch_picks_sync, user_id)


# ---------- /mypicks (lives here) ----------

async def mypicks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /mypicks — show the requesting user's saved picks.

    - Early stub reply proves wiring
    - Async-safe DB access (service-injected or to_thread)
    """
    msg = update.effective_message or update.message
    try:
        log.info(
            "mypicks: entered handler user_id=%s chat_id=%s",
            getattr(update.effective_user, "id", None),
            getattr(update.effective_chat, "id", None),
        )

        if msg:
            await msg.reply_text("✅ /mypicks handler reached. Fetching your picks...")

        picks = await _load_user_picks(update, context)
        out_text = _format_user_picks(picks)

        if msg:
            await msg.reply_text(out_text, disable_web_page_preview=True)

    except Exception as e:  # pragma: no cover
        log.exception("mypicks: crashed: %s", e)
        if msg:
            try:
                await msg.reply_text("❌ Sorry, /mypicks failed. Check logs.")
            except Exception:
                pass


# ---------- /myprops (prop bets) ----------

async def myprops(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /myprops — show the requesting user's prop bet picks for the current week.
    """
    msg = update.effective_message or update.message
    chat_id = str(update.effective_chat.id)

    try:
        app = create_app()
        with app.app_context():
            # Find the participant
            participant = db.session.execute(
                T("SELECT id, name FROM participants WHERE telegram_chat_id = :c"),
                {"c": chat_id},
            ).mappings().first()

            if not participant:
                await msg.reply_text("You're not linked yet. Send /start first.")
                return

            # Get current season/week
            season_year = db.session.execute(T("SELECT MAX(season_year) FROM weeks")).scalar()
            # Find the latest week with props
            week_number = db.session.execute(
                T("""
                    SELECT DISTINCT w.week_number
                    FROM weeks w
                    JOIN prop_bets pb ON pb.week_id = w.id
                    WHERE w.season_year = :y
                    ORDER BY w.week_number DESC
                    LIMIT 1
                """),
                {"y": season_year},
            ).scalar()

            if not week_number:
                await msg.reply_text("No prop bets available yet.")
                return

            # Get all props and picks for this week
            rows = db.session.execute(
                T("""
                    SELECT pb.id, pb.game_label, pb.description, pb.option_a, pb.option_b, pb.result,
                           pp.selected_option
                    FROM prop_bets pb
                    JOIN weeks w ON w.id = pb.week_id
                    LEFT JOIN prop_picks pp ON pp.prop_bet_id = pb.id AND pp.participant_id = :pid
                    WHERE w.season_year = :y AND w.week_number = :w
                    ORDER BY pb.game_label, pb.id
                """),
                {"pid": participant["id"], "y": season_year, "w": week_number},
            ).mappings().all()

            if not rows:
                await msg.reply_text(f"No props found for Week {week_number}.")
                return

            # Format output
            lines = [f"🎯 Your Props - Week {week_number}\n"]
            picked_count = 0
            correct_count = 0

            for r in rows:
                label = r["game_label"] or ""
                pick = r["selected_option"]
                result = r["result"]

                if pick:
                    picked_count += 1
                    if result:
                        if pick.upper() == result.upper():
                            status = "✅"
                            correct_count += 1
                        else:
                            status = "❌"
                    else:
                        status = "📝"
                    lines.append(f"{status} {label}: {r['description'][:35]}... → {pick}")
                else:
                    lines.append(f"⬜ {label}: {r['description'][:35]}...")

            lines.append(f"\nPicked: {picked_count}/{len(rows)}")
            if correct_count > 0:
                lines.append(f"Correct: {correct_count}")

            await msg.reply_text("\n".join(lines))

    except Exception as e:
        log.exception("myprops: crashed: %s", e)
        if msg:
            try:
                await msg.reply_text("❌ Sorry, /myprops failed. Check logs.")
            except Exception:
                pass

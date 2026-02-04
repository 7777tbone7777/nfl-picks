#!/usr/bin/env python3
"""
One-off script to fix Super Bowl game, add all props, and send to participants.
Run on Heroku: heroku run python fix_superbowl.py -a nfl-picks
"""
import os
import json
import urllib.request

# --- CONFIG ---
AWAY_TEAM = "Seattle Seahawks"  # NFC
HOME_TEAM = "New England Patriots"  # AFC
FAVORITE = "Seattle Seahawks"
SPREAD = 4.5  # Seahawks -4.5

# All 20 Super Bowl props
PROPS = [
    ("SB", "Total Points Over/Under 45.5", "OVER", "UNDER"),
    ("SB", "First team to score", "Seahawks", "Patriots"),
    ("SB", "Will there be a safety?", "YES", "NO"),
    ("SB", "Will the game go to overtime?", "YES", "NO"),
    ("SB", "Will there be a successful 2-point conversion?", "YES", "NO"),
    ("SB", "Kenneth Walker III rushing yards O/U 73.5", "OVER", "UNDER"),
    ("SB", "Jaxon Smith-Njigba receptions O/U 6.5", "OVER", "UNDER"),
    ("SB", "Drake Maye rushing yards O/U 37.5", "OVER", "UNDER"),
    ("SB", "Will Kenneth Walker III score a TD?", "YES", "NO"),
    ("SB", "Will Jaxon Smith-Njigba score a TD?", "YES", "NO"),
    ("SB", "Will Rhamondre Stevenson score a TD?", "YES", "NO"),
    ("SB", "Winning margin O/U 6.5 points", "OVER", "UNDER"),
    ("SB", "First half total points O/U 23.5", "OVER", "UNDER"),
    ("SB", "Total touchdowns in game O/U 5.5", "OVER", "UNDER"),
    ("SB", "Longest TD of the game O/U 39.5 yards", "OVER", "UNDER"),
    ("SB", "Coin toss result", "HEADS", "TAILS"),
    ("SB", "National Anthem length (Charlie Puth) O/U 119.5 sec", "OVER", "UNDER"),
    ("SB", "Gatorade shower color", "ORANGE", "OTHER"),
    ("SB", "First score of the game", "TOUCHDOWN", "FIELD GOAL"),
    ("SB", "Will either team score 3+ unanswered TDs?", "YES", "NO"),
    ("SB", "Will there be a lead change in 4th quarter?", "YES", "NO"),
]


def main():
    from flask_app import create_app
    from models import db
    from sqlalchemy import text as T

    app = create_app()
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    def send_message(chat_id, text, reply_markup=None):
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{TELEGRAM_API}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    with app.app_context():
        # 1) Find the current season
        season = db.session.execute(T("SELECT MAX(season_year) FROM weeks")).scalar()
        print(f"Season: {season}")

        # 2) Find the game with NFC @ AFC (or any upcoming game)
        game = db.session.execute(
            T("""
                SELECT g.id, g.week_id, g.away_team, g.home_team, g.game_time, w.week_number
                FROM games g
                JOIN weeks w ON w.id = g.week_id
                WHERE w.season_year = :y
                  AND (
                    (LOWER(g.away_team) = 'nfc' AND LOWER(g.home_team) = 'afc')
                    OR g.game_time > NOW()
                  )
                ORDER BY g.game_time
                LIMIT 1
            """),
            {"y": season},
        ).mappings().first()

        if not game:
            print("ERROR: No upcoming game found!")
            return

        game_id = game["id"]
        week_id = game["week_id"]
        week_number = game["week_number"]
        game_time = game["game_time"]

        print(f"Found game ID {game_id}: {game['away_team']} @ {game['home_team']}")
        print(f"Week {week_number}, game_time: {game_time}")

        # 3) Update the game with correct teams and spread
        db.session.execute(
            T("""
                UPDATE games
                SET away_team = :away,
                    home_team = :home,
                    favorite_team = :fav,
                    spread_pts = :spread
                WHERE id = :gid
            """),
            {
                "away": AWAY_TEAM,
                "home": HOME_TEAM,
                "fav": FAVORITE,
                "spread": SPREAD,
                "gid": game_id,
            },
        )
        db.session.commit()
        print(f"✅ Updated game {game_id}: {AWAY_TEAM} @ {HOME_TEAM} ({FAVORITE} -{SPREAD})")

        # 4) Delete any existing props for this week (fresh start)
        deleted = db.session.execute(
            T("DELETE FROM prop_picks WHERE prop_bet_id IN (SELECT id FROM prop_bets WHERE week_id = :wid)"),
            {"wid": week_id},
        ).rowcount
        deleted_props = db.session.execute(
            T("DELETE FROM prop_bets WHERE week_id = :wid"),
            {"wid": week_id},
        ).rowcount
        db.session.commit()
        print(f"🗑️  Cleared {deleted_props} existing props and {deleted} picks for week {week_number}")

        # 5) Create all props
        prop_ids = []
        for label, desc, opt_a, opt_b in PROPS:
            result = db.session.execute(
                T("""
                    INSERT INTO prop_bets (week_id, game_label, description, option_a, option_b, sent)
                    VALUES (:wid, :label, :desc, :opt_a, :opt_b, false)
                    RETURNING id
                """),
                {"wid": week_id, "label": label, "desc": desc, "opt_a": opt_a, "opt_b": opt_b},
            )
            prop_id = result.scalar()
            prop_ids.append((prop_id, desc, opt_a, opt_b))
            print(f"  ✅ Created prop {prop_id}: {desc}")

        db.session.commit()
        print(f"\n✅ Created {len(prop_ids)} props total")

        # 6) Get all participants
        participants = db.session.execute(
            T("SELECT id, name, telegram_chat_id FROM participants WHERE telegram_chat_id IS NOT NULL")
        ).mappings().all()

        print(f"\n📤 Sending to {len(participants)} participants...")

        # 7) Format game time
        from zoneinfo import ZoneInfo
        from datetime import timezone

        def format_pt(dt):
            if not dt:
                return "TBD"
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local = dt.astimezone(ZoneInfo("America/Los_Angeles"))
            return local.strftime("%a %m/%d %I:%M %p PT")

        game_time_str = format_pt(game_time)
        spread_label = f"{FAVORITE} -{SPREAD}"

        # 8) Build game message
        game_text = f"🏈 SUPER BOWL LX 🏈\n\n{AWAY_TEAM} @ {HOME_TEAM}\n{game_time_str}\n{spread_label}"
        game_kb = {
            "inline_keyboard": [
                [{"text": AWAY_TEAM, "callback_data": f"pick:{game_id}:{AWAY_TEAM}"}],
                [{"text": HOME_TEAM, "callback_data": f"pick:{game_id}:{HOME_TEAM}"}],
            ]
        }

        # 9) Send game + all props to each participant
        for p in participants:
            chat_id = p["telegram_chat_id"]
            name = p["name"]

            # Send game pick
            try:
                send_message(chat_id, game_text, game_kb)
                print(f"  ✅ {name}: Game sent")
            except Exception as e:
                print(f"  ❌ {name}: Game failed - {e}")

            # Send all props
            for i, (prop_id, desc, opt_a, opt_b) in enumerate(prop_ids, 1):
                prop_text = f"🎯 SUPER BOWL PROP #{i} 🎯\n\n{desc}"
                prop_kb = {
                    "inline_keyboard": [
                        [{"text": opt_a, "callback_data": f"prop:{prop_id}:{opt_a}"}],
                        [{"text": opt_b, "callback_data": f"prop:{prop_id}:{opt_b}"}],
                    ]
                }
                try:
                    send_message(chat_id, prop_text, prop_kb)
                except Exception as e:
                    print(f"  ❌ {name}: Prop #{i} failed - {e}")

            print(f"  ✅ {name}: All {len(prop_ids)} props sent")

        # 10) Mark all props as sent
        db.session.execute(T("UPDATE prop_bets SET sent = true WHERE week_id = :wid"), {"wid": week_id})
        db.session.commit()

        print(f"\n🎉 Done! Sent Super Bowl game + {len(prop_ids)} props to {len(participants)} participants.")


if __name__ == "__main__":
    main()

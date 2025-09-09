import os
from datetime import datetime
from flask import url_for
from twilio.rest import Client
import requests
from models import db, Participant, Week, Game, Pick, Reminder
from sqlalchemy import and_

# --- MOVED FROM NFL_DATA.PY ---
def update_scores_for_week(week_number, season_year, app):
    with app.app_context():
        week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()
        if not week:
            print(f"Week {week_number} for {season_year} not found.")
            return

        print(f"Updating scores for Week {week_number}...")
        url = f"http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?week={week_number}&seasontype=2&year={season_year}"
        try:
            data = requests.get(url).json()
        except Exception as e:
            print(f"ERROR: Failed to fetch ESPN scoreboard for scores: {e}")
            return
        
        updated_count = 0
        for event in data.get('events', []):
            game = Game.query.filter_by(espn_game_id=event.get('id')).first()
            if game and event.get('status', {}).get('type', {}).get('completed', False):
                comp = (event.get("competitions") or [{}])[0]
                home = next((c for c in comp.get('competitors', []) if c.get('homeAway') == 'home'), None)
                away = next((c for c in comp.get('competitors', []) if c.get('homeAway') == 'away'), None)
                
                if home and away and home.get('score') is not None and away.get('score') is not None:
                    game.home_score = int(home['score'])
                    game.away_score = int(away['score'])
                    game.status = 'final'
                    updated_count += 1
        db.session.commit()
        print(f"Updated scores for {updated_count} final games.")
# --- END MOVED SECTION ---

def send_sms(to_phone, message):
    # (This function is correct and remains the same)
    TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
    TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
    TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        print(f"Twilio not configured. Would send to {to_phone}: {message}")
        return True
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(body=message, from_=TWILIO_PHONE_NUMBER, to=to_phone)
        return True
    except Exception as e:
        print(f"SMS Error: {e}")
        return False

def send_week_launch_sms(week_number, app):
    with app.app_context():
        participants = Participant.query.all()
        for p in participants:
            url = url_for('picks_form', week_number=week_number, participant_name=p.name.lower(), _external=True)
            message = f"NFL Picks Week {week_number} is live! Make your picks: {url}"
            send_sms(p.phone, message)

def calculate_and_send_results(app):
    with app.app_context():
        latest_week = Week.query.order_by(Week.week_number.desc()).first()
        if not latest_week:
            print("No weeks found.")
            return

        week_to_score = latest_week.week_number
        season_to_score = latest_week.season_year
        
        update_scores_for_week(week_to_score, season_to_score, app)

        games = Game.query.filter_by(week_id=latest_week.id, status='final').all()
        for game in games:
            winner = None
            if game.home_score is not None and game.away_score is not None:
                if game.home_score > game.away_score: winner = game.home_team
                elif game.away_score > game.home_score: winner = game.away_team
            if winner is None: continue

            for pick in Pick.query.filter_by(game_id=game.id).all():
                pick.result = 'W' if pick.picked_team == winner else 'L'
        
        db.session.commit()
        print(f"Scored all final games for Week {week_to_score}.")

        for p in Participant.query.all():
            wins = Pick.query.filter(and_(Pick.participant_id == p.id, Pick.result == 'W')).join(Game).filter(Game.week_id == latest_week.id).count()
            losses = Pick.query.filter(and_(Pick.participant_id == p.id, Pick.result == 'L')).join(Game).filter(Game.week_id == latest_week.id).count()
            message = f"NFL Picks Week {week_to_score} Results: {p.name}, you went {wins}-{losses}!"
            send_sms(p.phone, message)
            print(f"Sent results to {p.name}")

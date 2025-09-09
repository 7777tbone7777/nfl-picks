import os
from datetime import datetime
from flask import url_for
from twilio.rest import Client
from models import db, Participant, Week, Game, Pick, Reminder
from sqlalchemy import and_
from nfl_data import update_scores_for_week

def send_sms(to_phone, message):
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
            message = f"NFL Picks Week {week_number} is live! Make your picks: {url} (Deadline: Thu 6PM ET)"
            send_sms(p.phone, message)

def calculate_and_send_results(app):
    with app.app_context():
        latest_week = Week.query.order_by(Week.week_number.desc()).first()
        if not latest_week:
            print("No weeks found.")
            return

        week_to_score = latest_week.week_number
        season_to_score = latest_week.season_year
        
        update_scores_for_week(week_to_score, season_to_score)

        games = Game.query.filter_by(week_id=latest_week.id, status='final').all()
        for game in games:
            winner = None
            if game.home_score is not None and game.away_score is not None:
                if game.home_score > game.away_score:
                    winner = game.home_team
                elif game.away_score > game.home_score:
                    winner = game.away_team
            
            if winner is None:
                continue

            picks_for_game = Pick.query.filter_by(game_id=game.id).all()
            for pick in picks_for_game:
                pick.result = 'W' if pick.picked_team == winner else 'L'
        
        db.session.commit()
        print(f"Scored all final games for Week {week_to_score}.")

        participants = Participant.query.all()
        for p in participants:
            wins = Pick.query.filter(and_(Pick.participant_id == p.id, Pick.result == 'W')).join(Game).filter(Game.week_id == latest_week.id).count()
            losses = Pick.query.filter(and_(Pick.participant_id == p.id, Pick.result == 'L')).join(Game).filter(Game.week_id == latest_week.id).count()
            
            message = f"NFL Picks Week {week_to_score} Results: {p.name}, you went {wins}-{losses}! See full results on the admin page."
            send_sms(p.phone, message)
            print(f"Sent results to {p.name}")

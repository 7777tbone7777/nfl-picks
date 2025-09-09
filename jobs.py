import os
from datetime import datetime
from flask import url_for
from twilio.rest import Client
import requests
from models import db, Participant, Week, Game, Pick, Reminder
from sqlalchemy import and_

def send_sms(to_phone, message):
    # --- THIS CODE WAS MISSING ---
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
    # --- END MISSING CODE ---

def send_week_launch_sms(week_number, app):
    with app.app_context():
        participants = Participant.query.all()
        for p in participants:
            url = url_for('picks_form', week_number=week_number, participant_name=p.name.lower(), _external=True)
            message = f"NFL Picks Week {week_number} is live! Make your picks: {url}"
            send_sms(p.phone, message)

def check_and_send_reminders(app):
    with app.app_context():
        now = datetime.utcnow()
        current_week = Week.query.filter(Week.picks_deadline > now).order_by(Week.week_number).first()
        if not current_week:
            print("No current week for reminders.")
            return

        games_count = Game.query.filter_by(week_id=current_week.id).count()
        if games_count == 0:
            return

        participants = Participant.query.all()
        for p in participants:
            picks_count = Pick.query.filter_by(participant_id=p.id).join(Game).filter(Game.week_id==current_week.id).count()
            if picks_count < games_count:
                hours_left = (current_week.picks_deadline - now).total_seconds() / 3600
                reminder_type = 'thursday' if hours_left <= 48 else 'tuesday'
                
                if not Reminder.query.filter_by(participant_id=p.id, week_id=current_week.id, reminder_type=reminder_type).first():
                    missing_count = games_count - picks_count
                    url_path = 'urgent_picks' if reminder_type == 'thursday' else 'picks_form'
                    url = url_for(url_path, week_number=current_week.week_number, participant_name=p.name.lower(), _external=True)
                    
                    message = f"FINAL CALL {p.name}! {missing_count} games still unpicked." if reminder_type == 'thursday' else f"Hey {p.name}! Reminder, you're missing {missing_count} picks."
                    message += f" Link: {url}"

                    if send_sms(p.phone, message):
                        db.session.add(Reminder(participant_id=p.id, week_id=current_week.id, reminder_type=reminder_type))
        db.session.commit()
        print("Reminder check complete.")

def calculate_and_send_results(app):
    with app.app_context():
        latest_week = Week.query.order_by(Week.week_number.desc()).first()
        if not latest_week:
            print("No weeks found.")
            return

        week_to_score = latest_week.week_number
        season_to_score = latest_week.season_year
        
        # update_scores_for_week is not defined in this file, so we call it carefully
        try:
            from nfl_data import update_scores_for_week
            update_scores_for_week(week_to_score, season_to_score)
        except ImportError:
            print("Warning: update_scores_for_week function not found. Skipping score updates.")


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

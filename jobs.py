import os
from datetime import datetime
from flask import url_for
from twilio.rest import Client
import requests
from models import db, Participant, Week, Game, Pick, Reminder
from sqlalchemy import and_

# (All the other functions in jobs.py remain the same)
# ...
def send_week_launch_sms(week_number, app):
    # ...
    
def calculate_and_send_results(app):
    # ...

# --- ADD THIS FUNCTION TO THE FILE ---
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

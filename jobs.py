# jobs.py
import os
from datetime import datetime
from sqlalchemy import func
from flask import url_for
from twilio.rest import Client
from models import db, Participant, Week, Game, Pick, Reminder

# Twilio setup (still here until you migrate to Telegram)
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_PHONE_NUMBER")

client = None
if TWILIO_SID and TWILIO_AUTH:
    client = Client(TWILIO_SID, TWILIO_AUTH)


def send_sms(to, body):
    """Send SMS via Twilio if configured, otherwise just print."""
    if client and TWILIO_FROM:
        message = client.messages.create(body=body, from_=TWILIO_FROM, to=to)
        print(f"Sent SMS: {message.sid}")
    else:
        print(f"[MOCK SMS to {to}] {body}")


def send_week_launch_sms(week: Week):
    """Send initial notification to participants for new week."""
    for p in Participant.query.all():
        link = url_for("picks_form", week_number=week.week_number, participant_name=p.name, _external=True)
        body = f"🏈 NFL Picks Week {week.week_number} is open! Submit before {week.picks_deadline}. {link}"
        send_sms(p.phone, body)

        r = Reminder(week_id=week.id, participant_id=p.id, kind="launch", channel="sms")
        db.session.add(r)

    db.session.commit()


def check_and_send_reminders():
    """Send reminders for upcoming picks deadlines."""
    now = datetime.utcnow()
    upcoming = Week.query.filter(Week.picks_deadline > now).order_by(Week.picks_deadline).first()

    if not upcoming:
        return

    for p in Participant.query.all():
        submitted = Pick.query.join(Game).filter(
            Pick.participant_id == p.id, Game.week_id == upcoming.id
        ).first()

        if not submitted:
            link = url_for("urgent_picks", week_number=upcoming.week_number, participant_name=p.name, _external=True)
            body = f"⚠️ Reminder: Submit your NFL picks for Week {upcoming.week_number} before {upcoming.picks_deadline}! {link}"
            send_sms(p.phone, body)

            r = Reminder(week_id=upcoming.id, participant_id=p.id, kind="deadline", channel="sms")
            db.session.add(r)

    db.session.commit()


def calculate_and_send_results():
    """Calculate weekly results and send to each participant."""
    latest_week = Week.query.order_by(Week.week_number.desc()).first()
    if not latest_week:
        return

    games = Game.query.filter_by(week_id=latest_week.id, status="final").all()
    if not games:
        return

    # Ensure winners are set
    for g in games:
        if g.home_score is not None and g.away_score is not None:
            if g.home_score > g.away_score:
                g.winner = g.home_team
            elif g.away_score > g.home_score:
                g.winner = g.away_team
    db.session.commit()

    for p in Participant.query.all():
        wins = db.session.query(func.count(Pick.id)).join(Game).filter(
            Pick.participant_id == p.id,
            Game.week_id == latest_week.id,
            Game.winner.isnot(None),
            Pick.selected_team == Game.winner,
        ).scalar()

        losses = db.session.query(func.count(Pick.id)).join(Game).filter(
            Pick.participant_id == p.id,
            Game.week_id == latest_week.id,
            Game.winner.isnot(None),
            Pick.selected_team != Game.winner,
        ).scalar()

        body = f"📊 Week {latest_week.week_number} Results: {p.name}, you went {wins}-{losses}!"
        send_sms(p.phone, body)

        r = Reminder(week_id=latest_week.id, participant_id=p.id, kind="results", channel="sms")
        db.session.add(r)

    db.session.commit()


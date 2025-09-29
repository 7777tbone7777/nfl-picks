import os
from datetime import datetime, timedelta

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from twilio.rest import Client

app = Flask(__name__)

# Configuration
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "postgresql://username:password@localhost/nfl_picks"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "your-secret-key-here")

# Twilio configuration
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

db = SQLAlchemy(app)


# Models
class Participant(db.Model):
    __tablename__ = "participants"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    phone = db.Column(db.String(15), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Week(db.Model):
    __tablename__ = "weeks"
    id = db.Column(db.Integer, primary_key=True)
    week_number = db.Column(db.Integer, nullable=False)
    season_year = db.Column(db.Integer, nullable=False)
    picks_deadline = db.Column(db.DateTime, nullable=False)
    reminder_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Game(db.Model):
    __tablename__ = "games"
    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(db.Integer, db.ForeignKey("weeks.id"), nullable=False)
    home_team = db.Column(db.String(50), nullable=False)
    away_team = db.Column(db.String(50), nullable=False)
    game_time = db.Column(db.DateTime, nullable=False)
    home_score = db.Column(db.Integer)
    away_score = db.Column(db.Integer)
    status = db.Column(db.String(20), default="scheduled")  # scheduled, in_progress, final
    espn_game_id = db.Column(db.String(20))


class Pick(db.Model):
    __tablename__ = "picks"
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participants.id"), nullable=False)
    game_id = db.Column(db.Integer, db.ForeignKey("games.id"), nullable=False)
    picked_team = db.Column(db.String(50), nullable=False)
    result = db.Column(db.String(4))  # W, L, T, NP
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Reminder(db.Model):
    __tablename__ = "reminders"
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participants.id"), nullable=False)
    week_id = db.Column(db.Integer, db.ForeignKey("weeks.id"), nullable=False)
    reminder_type = db.Column(db.String(20), nullable=False)  # tuesday, thursday
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)


# Routes
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/picks/week<int:week_number>/<participant_name>")
def picks_form(week_number, participant_name):
    current_year = datetime.now().year
    participant = Participant.query.filter_by(name=participant_name.title()).first()
    if not participant:
        return f"Participant {participant_name} not found", 404

    week = Week.query.filter_by(week_number=week_number, season_year=current_year).first()
    if not week:
        return f"Week {week_number} not found", 404

    if datetime.utcnow() > week.picks_deadline:
        return render_template("deadline_passed.html", week=week)

    games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()

    existing_picks = {
        p.game_id: p.picked_team
        for p in Pick.query.filter_by(participant_id=participant.id)
        .join(Game)
        .filter(Game.week_id == week.id)
        .all()
    }

    return render_template(
        "picks_form.html",
        participant=participant,
        week=week,
        games=games,
        existing_picks=existing_picks,
    )


@app.route("/picks/week<int:week_number>/<participant_name>/urgent")
def urgent_picks(week_number, participant_name):
    current_year = datetime.now().year
    participant = Participant.query.filter_by(name=participant_name.title()).first()
    if not participant:
        return f"Participant {participant_name} not found", 404

    week = Week.query.filter_by(week_number=week_number, season_year=current_year).first()
    if not week:
        return f"Week {week_number} not found", 404

    all_games = Game.query.filter_by(week_id=week.id).all()
    picked_game_ids = {
        p.game_id
        for p in Pick.query.filter_by(participant_id=participant.id)
        .join(Game)
        .filter(Game.week_id == week.id)
        .all()
    }
    unpicked_games = [g for g in all_games if g.id not in picked_game_ids]

    return render_template(
        "urgent_picks.html", participant=participant, week=week, games=unpicked_games
    )


@app.route("/submit_picks", methods=["POST"])
def submit_picks():
    data = request.json
    participant_id = data["participant_id"]
    picks = data.get("picks", {})

    for game_id, picked_team in picks.items():
        existing_pick = Pick.query.filter_by(participant_id=participant_id, game_id=game_id).first()

        if existing_pick:
            existing_pick.picked_team = picked_team
        else:
            new_pick = Pick(participant_id=participant_id, game_id=game_id, picked_team=picked_team)
            db.session.add(new_pick)

    db.session.commit()
    return jsonify({"status": "success"})


@app.route("/admin")
def admin():
    current_year = datetime.now().year
    weeks = Week.query.filter_by(season_year=current_year).order_by(Week.week_number).all()
    participants = Participant.query.all()
    return render_template("admin.html", weeks=weeks, participants=participants)


@app.route("/admin/send_launch_sms", methods=["POST"])
def send_launch_sms_route():
    data = request.json
    week_number = data["week_number"]
    send_week_launch_sms(week_number)
    return jsonify({"status": "success", "message": f"Launch SMS sent for Week {week_number}"})


@app.route("/admin/status/<int:week_number>")
def week_status(week_number):
    current_year = datetime.now().year
    week = Week.query.filter_by(week_number=week_number, season_year=current_year).first()
    if not week:
        return jsonify({"error": "Week not found"}), 404

    participants = Participant.query.all()
    games_count = Game.query.filter_by(week_id=week.id).count()

    status_data = [
        {
            "name": p.name,
            "picks_made": Pick.query.filter_by(participant_id=p.id)
            .join(Game)
            .filter(Game.week_id == week.id)
            .count(),
            "total_games": games_count,
            "complete": Pick.query.filter_by(participant_id=p.id)
            .join(Game)
            .filter(Game.week_id == week.id)
            .count()
            == games_count,
        }
        for p in participants
    ]

    return jsonify({"week_number": week_number, "participants": status_data})


# SMS & Scheduler Functions
def send_sms(to_phone, message):
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


def send_week_launch_sms(week_number):
    with app.app_context():
        participants = Participant.query.all()
        for p in participants:
            url = url_for(
                "picks_form",
                week_number=week_number,
                participant_name=p.name.lower(),
                _external=True,
            )
            message = f"NFL Picks Week {week_number} is live! Make your picks: {url} (Deadline: Thu 6PM ET)"
            send_sms(p.phone, message)


def check_and_send_reminders():
    with app.app_context():
        now = datetime.utcnow()
        current_week = (
            Week.query.filter(Week.picks_deadline > now).order_by(Week.week_number).first()
        )
        if not current_week:
            return

        games_count = Game.query.filter_by(week_id=current_week.id).count()
        if games_count == 0:
            return

        participants = Participant.query.all()
        for p in participants:
            picks_count = (
                Pick.query.filter_by(participant_id=p.id)
                .join(Game)
                .filter(Game.week_id == current_week.id)
                .count()
            )
            if picks_count < games_count:
                hours_left = (current_week.picks_deadline - now).total_seconds() / 3600
                reminder_type = "thursday" if hours_left <= 48 else "tuesday"

                if not Reminder.query.filter_by(
                    participant_id=p.id,
                    week_id=current_week.id,
                    reminder_type=reminder_type,
                ).first():
                    missing_count = games_count - picks_count
                    url_path = "urgent_picks" if reminder_type == "thursday" else "picks_form"
                    url = url_for(
                        url_path,
                        week_number=current_week.week_number,
                        participant_name=p.name.lower(),
                        _external=True,
                    )

                    if reminder_type == "thursday":
                        message = f"FINAL CALL {p.name}! {missing_count} games still unpicked. Deadline is tonight: {url}"
                    else:
                        message = f"Hey {p.name}! Just a reminder, you're missing {missing_count} picks for Week {current_week.week_number}. {url}"

                    if send_sms(p.phone, message):
                        db.session.add(
                            Reminder(
                                participant_id=p.id,
                                week_id=current_week.id,
                                reminder_type=reminder_type,
                            )
                        )
        db.session.commit()


# --- Main Execution ---
if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    # Gentle reminder on Tuesday evenings
    scheduler.add_job(
        func=check_and_send_reminders, trigger="cron", day_of_week="tue", hour=20
    )  # 8 PM UTC
    # Urgent reminder on Thursday evenings
    scheduler.add_job(
        func=check_and_send_reminders, trigger="cron", day_of_week="thu", hour=18
    )  # 6 PM UTC
    scheduler.start()

    with app.app_context():
        db.create_all()

    app.run(debug=True)

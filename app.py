from flask import Flask, render_template, request, jsonify, url_for
from datetime import datetime
import os
from models import db, Participant, Week, Game, Pick
from jobs import send_week_launch_sms, check_and_send_reminders

def create_app():
    app = Flask(__name__, template_folder="templates")
    
    database_url = os.environ.get('DATABASE_URL', 'postgresql://username:password@localhost/nfl_picks')
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret')
    
    db.init_app(app)

    # --- Routes ---
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/picks/week<int:week_number>/<participant_name>')
    def picks_form(week_number, participant_name):
        current_year = datetime.now().year
        participant = Participant.query.filter_by(name=participant_name.title()).first()
        if not participant: return f"Participant {participant_name} not found", 404
        week = Week.query.filter_by(week_number=week_number, season_year=current_year).first()
        if not week: return f"Week {week_number} not found", 404
        if datetime.utcnow() > week.picks_deadline: return render_template('deadline_passed.html', week=week)
        games = Game.query.filter_by(week_id=week.id).order_by(Game.game_time).all()
        existing_picks = {p.game_id: p.picked_team for p in Pick.query.filter_by(participant_id=participant.id).join(Game).filter(Game.week_id==week.id).all()}
        return render_template('picks_form.html', participant=participant, week=week, games=games, existing_picks=existing_picks)

    @app.route('/picks/week<int:week_number>/<participant_name>/urgent')
    def urgent_picks(week_number, participant_name):
        current_year = datetime.now().year
        participant = Participant.query.filter_by(name=participant_name.title()).first()
        if not participant: return f"Participant {participant_name} not found", 404
        week = Week.query.filter_by(week_number=week_number, season_year=current_year).first()
        if not week: return f"Week {week_number} not found", 404
        all_games = Game.query.filter_by(week_id=week.id).all()
        picked_game_ids = {p.game_id for p in Pick.query.filter_by(participant_id=participant.id).join(Game).filter(Game.week_id==week.id).all()}
        unpicked_games = [g for g in all_games if g.id not in picked_game_ids]
        return render_template('urgent_picks.html', participant=participant, week=week, games=unpicked_games)

    @app.route('/submit_picks', methods=['POST'])
    def submit_picks():
        data = request.json
        participant_id = data['participant_id']
        picks = data.get('picks', {})
        for game_id, picked_team in picks.items():
            existing_pick = Pick.query.filter_by(participant_id=participant_id, game_id=game_id).first()
            if existing_pick:
                existing_pick.picked_team = picked_team
            else:
                db.session.add(Pick(participant_id=participant_id, game_id=game_id, picked_team=picked_team))
        db.session.commit()
        return jsonify({'status': 'success'})

    @app.route('/admin')
    def admin():
        current_year = datetime.now().year
        weeks = Week.query.filter_by(season_year=current_year).order_by(Week.week_number).all()
        participants = Participant.query.all()
        return render_template('admin.html', weeks=weeks, participants=participants)

    @app.route('/admin/send_launch_sms', methods=['POST'])
    def send_launch_sms_route():
        data = request.json
        week_number = data['week_number']
        send_week_launch_sms(week_number, app)
        return jsonify({'status': 'success'})

    @app.route('/admin/status/<int:week_number>')
    def week_status(week_number):
        current_year = datetime.now().year
        week = Week.query.filter_by(week_number=week_number, season_year=current_year).first()
        if not week: return jsonify({'error': 'Week not found'}), 404
        participants = Participant.query.all()
        games_count = Game.query.filter_by(week_id=week.id).count()
        status_data = [{'name': p.name, 'picks_made': Pick.query.filter_by(participant_id=p.id).join(Game).filter(Game.week_id==week.id).count(), 'total_games': games_count, 'complete': Pick.query.filter_by(participant_id=p.id).join(Game).filter(Game.week_id==week.id).count() == games_count} for p in participants]
        return jsonify({'week_number': week_number, 'participants': status_data})

    return app

app = create_app()

if __name__ == '__main__':
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=lambda: check_and_send_reminders(app), trigger="cron", day_of_week="tue", hour=20)
    scheduler.add_job(func=lambda: check_and_send_reminders(app), trigger="cron", day_of_week="thu", hour=18)
    scheduler.start()
    app.run(debug=True)

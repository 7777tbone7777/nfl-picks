from flask import Flask, render_template, request, jsonify, url_for
from datetime import datetime
import os
from models import db, Participant, Week, Game, Pick
from jobs import send_week_launch_sms, check_and_send_reminders # <-- UPDATED IMPORT

def create_app():
    app = Flask(__name__)
    
    # Configuration
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

    @app.route('/admin/send_launch_sms', methods=['POST'])
    def send_launch_sms_route():
        data = request.json
        week_number = data['week_number']
        send_week_launch_sms(week_number, app) # Pass app context
        return jsonify({'status': 'success', 'message': f'Launch SMS sent for Week {week_number}'})

    # ... (all your other routes remain the same) ...
    # (The `send_sms` and `check_and_send_reminders` functions are now in jobs.py)
    
    return app

# Main execution for gunicorn on Heroku
app = create_app()

if __name__ == '__main__':
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=lambda: check_and_send_reminders(app), trigger="cron", day_of_week="tue", hour=20)
    scheduler.add_job(func=lambda: check_and_send_reminders(app), trigger="cron", day_of_week="thu", hour=18)
    scheduler.start()
    app.run(debug=True)

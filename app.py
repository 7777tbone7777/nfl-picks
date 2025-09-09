from flask import Flask, render_template, request, jsonify, url_for
import os
from models import db, Participant, Week, Game, Pick
from jobs import send_week_launch_sms, check_and_send_reminders

def create_app():
    app = Flask(__name__)
    
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
        # Your routes go here, this is just an example
        return "Welcome to NFL Picks!"
    
    @app.route('/admin/send_launch_sms', methods=['POST'])
    def send_launch_sms_route():
        data = request.json
        week_number = data['week_number']
        send_week_launch_sms(week_number, app)
        return jsonify({'status': 'success'})

    # (Add all your other @app.route functions here)
    
    return app

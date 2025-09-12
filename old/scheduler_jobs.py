from flask_app import create_app
from jobs import send_week_launch_sms
from models import Week

# Example: manual trigger pattern; APScheduler wiring can be added later
if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        wk = Week.query.order_by(Week.week_number.desc()).first()
        if wk:
            send_week_launch_sms(wk)
            print(f"✅ Launch message sent for Week {wk.week_number}")
        else:
            print("No week found.")

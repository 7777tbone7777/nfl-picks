import sys
from datetime import datetime
from app import create_app, send_week_launch_sms
from models import db, Week
from nfl_data import fetch_and_create_week

def create_next_week_and_notify():
    """
    Finds the latest week in the database, calculates the next one,
    creates it, and sends the launch SMS.
    """
    app = create_app()
    with app.app_context():
        latest_week = Week.query.order_by(Week.week_number.desc()).first()
        
        if not latest_week:
            print("No existing weeks found. Please create the first week manually.")
            return

        next_week_number = latest_week.week_number + 1
        current_year = datetime.now().year
        
        print(f"Attempting to create Week {next_week_number}...")
        
        # Call the existing function from nfl_data.py
        fetch_and_create_week(next_week_number, current_year)

        # After creating the week, send the launch SMS
        print(f"Sending launch SMS for Week {next_week_number}...")
        send_week_launch_sms(next_week_number, app)
        print("Launch SMS job complete.")

if __name__ == '__main__':
    # This allows us to call different jobs from the same file
    if len(sys.argv) > 1:
        job = sys.argv[1]
        if job == 'create_next_week':
            create_next_week_and_notify()
        else:
            print(f"Unknown job: {job}")
    else:
        print("Usage: python scheduler_jobs.py <job_name>")

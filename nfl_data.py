import requests
import sys
from datetime import datetime
from app import create_app
from models import db, Week, Game

def fetch_and_create_week(week_number, season_year=2024):
    app = create_app()
    with app.app_context():
        if Week.query.filter_by(week_number=week_number, season_year=season_year).first():
            print(f"Week {week_number} for {season_year} already exists.")
            return

        print(f"Creating Week {week_number} for season {season_year}...")
        url = f"http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?week={week_number}&seasontype=2&year={season_year}"
        try:
            data = requests.get(url).json()
        except Exception as e:
            print(f"Error fetching data from ESPN API: {e}")
            return

        games_data = data.get('events', [])
        if not games_data:
            print("No games found for this week.")
            return
            
        first_thursday_game_time = min(
            (datetime.fromisoformat(e['date'].replace('Z', '+00:00')) for e in games_data if datetime.fromisoformat(e['date'].replace('Z', '+00:00')).weekday() == 3),
            default=None
        )

        if not first_thursday_game_time:
            print("Warning: No Thursday game found. Using a default deadline.")
            first_thursday_game_time = datetime.now() # Fallback
        
        new_week = Week(week_number=week_number, season_year=season_year, picks_deadline=first_thursday_game_time)
        db.session.add(new_week)
        db.session.commit()

        for event in games_data:
            comp = event['competitions'][0]
            home = next(c for c in comp['competitors'] if c['homeAway'] == 'home')
            away = next(c for c in comp['competitors'] if c['homeAway'] == 'away')
            db.session.add(Game(
                week_id=new_week.id,
                home_team=home['team']['displayName'],
                away_team=away['team']['displayName'],
                game_time=datetime.fromisoformat(event['date'].replace('Z', '+00:00')),
                espn_game_id=event['id']
            ))
        
        db.session.commit()
        print(f"Successfully created Week {week_number} with {len(games_data)} games.")

if __name__ == '__main__':
    if len(sys.argv) > 1:
        try:
            fetch_and_create_week(int(sys.argv[1]))
        except ValueError:
            print("Please provide a valid week number.")
    else:
        print("Usage: python nfl_data.py <week_number>")

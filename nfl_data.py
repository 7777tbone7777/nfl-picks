import requests
import sys
from datetime import datetime, timezone
from app import app, db, Week, Game

def fetch_and_create_week(week_number, season_year=2024):
    with app.app_context():
        # Check if the week already exists
        week = Week.query.filter_by(week_number=week_number, season_year=season_year).first()
        if week:
            print(f"Week {week_number} for {season_year} already exists.")
            return

        # Create the new week
        # Deadline is Thursday 8:15 PM ET, which is Friday 00:15 UTC
        # Find the actual Thursday game time to set a more accurate deadline
        
        print(f"Creating Week {week_number} for season {season_year}...")
        
        # Fetch game data from ESPN's public API
        url = f"http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?week={week_number}&seasontype=2&year={season_year}"
        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching data from ESPN API: {e}")
            return

        games_data = data.get('events', [])
        if not games_data:
            print("No games found for this week from the API.")
            return
            
        # Find the first Thursday game to set the deadline
        first_thursday_game_time = None
        for event in games_data:
            game_time_utc = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
            if game_time_utc.weekday() == 3: # Thursday
                if first_thursday_game_time is None or game_time_utc < first_thursday_game_time:
                    first_thursday_game_time = game_time_utc

        if not first_thursday_game_time:
             # Default to a generic Thursday evening if no Thursday game is found
             # This is a fallback and should be reviewed
             print("Warning: No Thursday game found to set deadline. Using a default.")
             first_thursday_game_time = datetime(season_year, 1, 1) # Placeholder
        
        new_week = Week(
            week_number=week_number,
            season_year=season_year,
            picks_deadline=first_thursday_game_time
        )
        db.session.add(new_week)
        db.session.commit() # Commit to get the new_week.id

        game_count = 0
        for event in games_data:
            competition = event['competitions'][0]
            game_time_utc = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
            
            home_team_data = next(c for c in competition['competitors'] if c['homeAway'] == 'home')
            away_team_data = next(c for c in competition['competitors'] if c['homeAway'] == 'away')
            
            new_game = Game(
                week_id=new_week.id,
                home_team=home_team_data['team']['displayName'],
                away_team=away_team_data['team']['displayName'],
                game_time=game_time_utc,
                espn_game_id=event['id']
            )
            db.session.add(new_game)
            game_count += 1
        
        db.session.commit()
        print(f"Successfully created Week {week_number} with {game_count} games.")
        print(f"Picks deadline is set to: {new_week.picks_deadline.strftime('%Y-%m-%d %H:%M:%S')} UTC")

if __name__ == '__main__':
    if len(sys.argv) > 1:
        try:
            week_num = int(sys.argv[1])
            fetch_and_create_week(week_num)
        except ValueError:
            print("Please provide a valid week number.")
    else:
        print("Usage: python nfl_data.py <week_number>")

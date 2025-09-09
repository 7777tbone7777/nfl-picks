from app import create_app, send_sms
from models import db, Week, Game, Participant, Pick
from nfl_data import update_scores_for_week
from sqlalchemy import and_

def calculate_and_send_results():
    app = create_app()
    with app.app_context():
        # Find the most recently completed week
        latest_week = Week.query.order_by(Week.week_number.desc()).first()
        if not latest_week:
            print("No weeks found.")
            return

        week_to_score = latest_week.week_number
        season_to_score = latest_week.season_year
        
        # 1. Update the scores from the API
        update_scores_for_week(week_to_score, season_to_score)

        # 2. Score the picks
        games = Game.query.filter_by(week_id=latest_week.id, status='final').all()
        for game in games:
            winner = None
            if game.home_score is not None and game.away_score is not None:
                if game.home_score > game.away_score:
                    winner = game.home_team
                elif game.away_score > game.home_score:
                    winner = game.away_team
            
            if winner is None:
                continue # Skip ties or games with no scores

            picks_for_game = Pick.query.filter_by(game_id=game.id).all()
            for pick in picks_for_game:
                if pick.picked_team == winner:
                    pick.result = 'W'
                else:
                    pick.result = 'L'
        
        db.session.commit()
        print(f"Scored all final games for Week {week_to_score}.")

        # 3. Send results to each participant
        participants = Participant.query.all()
        for p in participants:
            wins = Pick.query.filter(and_(Pick.participant_id == p.id, Pick.result == 'W')).join(Game).filter(Game.week_id == latest_week.id).count()
            losses = Pick.query.filter(and_(Pick.participant_id == p.id, Pick.result == 'L')).join(Game).filter(Game.week_id == latest_week.id).count()
            
            message = f"NFL Picks Week {week_to_score} Results: {p.name}, you went {wins}-{losses}! See full results on the admin page."
            send_sms(p.phone, message)
            print(f"Sent results to {p.name}")

if __name__ == '__main__':
    calculate_and_send_results()

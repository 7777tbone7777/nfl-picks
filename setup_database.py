from app import app, db, Participant, Week, Game
from datetime import datetime, timedelta

def setup_initial_data():
    with app.app_context():
        # Create all database tables if they don't exist
        db.create_all()
        
        # Add participants (update with real names/phones)
        participants_to_add = [
            {'name': 'Will', 'phone': '+15555555551'},
            {'name': 'Kevin', 'phone': '+15555555552'}, 
            {'name': 'Tony', 'phone': '+15555555553'}
        ]
        
        participants_in_db = []
        for p_data in participants_to_add:
            existing = Participant.query.filter_by(name=p_data['name']).first()
            if not existing:
                new_participant = Participant(name=p_data['name'], phone=p_data['phone'])
                db.session.add(new_participant)
                print(f"Added participant: {p_data['name']}")
                participants_in_db.append(new_participant)
            else:
                participants_in_db.append(existing)
        
        db.session.commit()
        print("Database setup complete!")
        
        # Print participant URLs for easy testing
        print("\n--- Test URLs ---")
        for p in participants_in_db:
            print(f"{p.name}: /picks/week<WEEK_NUMBER>/{p.name.lower()}")
        print("Replace <WEEK_NUMBER> with the week you create.")


if __name__ == '__main__':
    setup_initial_data()

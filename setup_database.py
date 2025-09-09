from app import create_app
from models import db, Participant

def setup_initial_data():
    app = create_app()
    with app.app_context():
        print("Creating database tables...")
        db.create_all()
        
        participants_to_add = [
            {'name': 'Will', 'phone': '+18185316200'},
            {'name': 'Kevin', 'phone': '+18185316200'}, 
            {'name': 'Tony', 'phone': '+18185316200'}
        ]
        
        for p_data in participants_to_add:
            if not Participant.query.filter_by(name=p_data['name']).first():
                db.session.add(Participant(name=p_data['name'], phone=p_data['phone']))
                print(f"Added participant: {p_data['name']}")
        
        db.session.commit()
        print("Database setup complete!")

if __name__ == '__main__':
    setup_initial_data()

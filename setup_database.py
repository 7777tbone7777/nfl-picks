# setup_database.py
from app import create_app
from models import db, Participant

# Create the Flask app using your factory
app = create_app()

with app.app_context():
    # Create all tables
    db.create_all()

    # Seed participants if they don’t exist
    for name in ["Kevin", "Will", "Tony"]:
        exists = Participant.query.filter_by(name=name).first()
        if not exists:
            db.session.add(Participant(name=name))
    
    db.session.commit()

print("✅ Database setup complete. Participants seeded.")


from flask_app import create_app
from models import Participant, db


def setup_initial_data():
    app = create_app()
    with app.app_context():
        print("Creating database tables...")
        db.create_all()

        for name, phone in [
            ("Will", "+18185316200"),
            ("Kevin", "+18185316200"),
            ("Tony", "+18185316200"),
        ]:
            if not Participant.query.filter_by(name=name).first():
                db.session.add(Participant(name=name, phone=phone))
                print(f"Added participant: {name}")

        db.session.commit()
        print("Database setup complete!")


if __name__ == "__main__":
    setup_initial_data()

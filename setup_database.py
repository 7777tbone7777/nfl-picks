# setup_database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, Participant
import os

# Get DB URL from environment (Heroku sets DATABASE_URL)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///nfl_picks.db")

# Create engine
engine = create_engine(DATABASE_URL)

# Create all tables
Base.metadata.create_all(engine)

# Optional: seed participants (Kevin, Will, Tony) if not already present
SessionLocal = sessionmaker(bind=engine)
session = SessionLocal()

participants = ["Kevin", "Will", "Tony"]
for name in participants:
    exists = session.query(Participant).filter_by(name=name).first()
    if not exists:
        session.add(Participant(name=name))

session.commit()
session.close()

print("✅ Database setup complete. Participants seeded.")


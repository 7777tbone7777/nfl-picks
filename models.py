# models.py
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Week(db.Model):
    __tablename__ = "weeks"

    id = db.Column(db.Integer, primary_key=True)
    week_number = db.Column(db.Integer, nullable=False)
    season_year = db.Column(db.Integer, nullable=False, index=True)

    # Not null in your DB, used for pick lock deadline
    picks_deadline = db.Column(db.DateTime, nullable=False)

    # Used by your reminder job
    reminder_sent = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    games = db.relationship(
        "Game",
        backref="week",
        lazy=True,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "week_number", "season_year", name="uq_weeks_week_season"
        ),
    )

    def __repr__(self) -> str:
        return f"<Week {self.season_year} W{self.week_number}>"


class Game(db.Model):
    __tablename__ = "games"

    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(
        db.Integer, db.ForeignKey("weeks.id", ondelete="CASCADE"), nullable=False, index=True
    )

    home_team = db.Column(db.String(64), nullable=False)
    away_team = db.Column(db.String(64), nullable=False)

    # Your code uses this for ordering & display
    game_time = db.Column(db.DateTime, nullable=False, index=True)

    home_score = db.Column(db.Integer)
    away_score = db.Column(db.Integer)

    status = db.Column(db.String(32), nullable=False, default="scheduled")

    # Optional but present in your schema / import script
    espn_game_id = db.Column(db.String(32), unique=True)

    picks = db.relationship(
        "Pick",
        backref="game",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Game {self.away_team} @ {self.home_team} {self.game_time}>"


class Participant(db.Model):
    __tablename__ = "participants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)

    # ---- NEW (nullable) ----
    telegram_chat_id = db.Column(db.String(64), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    picks = db.relationship(
        "Pick",
        backref="participant",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Participant {self.name}>"


class Pick(db.Model):
    __tablename__ = "picks"

    id = db.Column(db.Integer, primary_key=True)

    participant_id = db.Column(
        db.Integer, db.ForeignKey("participants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    game_id = db.Column(
        db.Integer, db.ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Must match one of the teams in the game
    selected_team = db.Column(db.String(64), nullable=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "participant_id", "game_id", name="uq_picks_participant_game"
        ),
    )

    def __repr__(self) -> str:
        return f"<Pick P{self.participant_id} G{self.game_id} {self.selected_team}>"


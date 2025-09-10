# models.py
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

# SQLAlchemy instance is initialized in app.create_app()
db = SQLAlchemy()


class Week(db.Model):
    __tablename__ = "weeks"

    id = db.Column(db.Integer, primary_key=True)
    week_number = db.Column(db.Integer, nullable=False)
    season_year = db.Column(db.Integer, nullable=False, index=True)
    # Stored as naive UTC datetime
    picks_deadline = db.Column(db.DateTime, nullable=False)
    reminder_sent = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    games = db.relationship(
        "Game",
        backref="week",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        db.UniqueConstraint("week_number", "season_year", name="uq_week_season"),
    )

    def __repr__(self) -> str:
        return f"<Week {self.season_year}-W{self.week_number} deadline={self.picks_deadline}>"


class Game(db.Model):
    __tablename__ = "games"

    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(
        db.Integer,
        db.ForeignKey("weeks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    espn_game_id = db.Column(db.String(32), unique=True, index=True)
    home_team = db.Column(db.String(64), nullable=False)
    away_team = db.Column(db.String(64), nullable=False)
    # Stored as naive UTC datetime
    game_time = db.Column(db.DateTime, nullable=False, index=True)
    status = db.Column(db.String(32), nullable=False, default="scheduled")

    # Result fields (populated when games go final)
    home_score = db.Column(db.Integer)
    away_score = db.Column(db.Integer)
    winner = db.Column(db.String(64))  # winning team name when final

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    picks = db.relationship(
        "Pick",
        backref="game",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        db.Index("ix_games_week_time", "week_id", "game_time"),
    )

    def __repr__(self) -> str:
        return f"<Game {self.away_team} @ {self.home_team} {self.game_time} ({self.status})>"


class Participant(db.Model):
    __tablename__ = "participants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False, unique=True)
    # Legacy field; keep nullable so existing rows are valid
    phone = db.Column(db.String(32))
    # New: Telegram chat id for bot DMs (nullable until user links)
    telegram_chat_id = db.Column(db.String(64))

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    picks = db.relationship(
        "Pick",
        backref="participant",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Participant {self.name}>"


class Pick(db.Model):
    __tablename__ = "picks"

    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(
        db.Integer,
        db.ForeignKey("participants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    game_id = db.Column(
        db.Integer,
        db.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Must match either Game.home_team or Game.away_team
    selected_team = db.Column(db.String(64), nullable=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "participant_id", "game_id", name="uq_pick_participant_game"
        ),
    )

    def __repr__(self) -> str:
        return f"<Pick p={self.participant_id} g={self.game_id} team={self.selected_team}>"


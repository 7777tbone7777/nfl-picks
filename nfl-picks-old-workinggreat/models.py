# models.py
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

# Initialized in app.create_app()
db = SQLAlchemy()


class Week(db.Model):
    __tablename__ = "weeks"

    id = db.Column(db.Integer, primary_key=True)
    week_number = db.Column(db.Integer, nullable=False)
    season_year = db.Column(db.Integer, nullable=False, index=True)
    # naive UTC
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
        db.Integer, db.ForeignKey("weeks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    espn_game_id = db.Column(db.String(32), unique=True, index=True)
    home_team = db.Column(db.String(64), nullable=False)
    away_team = db.Column(db.String(64), nullable=False)
    # naive UTC
    game_time = db.Column(db.DateTime, nullable=False, index=True)
    status = db.Column(db.String(32), nullable=False, default="scheduled")

    home_score = db.Column(db.Integer)
    away_score = db.Column(db.Integer)
    winner = db.Column(db.String(64))

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    picks = db.relationship(
        "Pick",
        backref="game",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (db.Index("ix_games_week_time", "week_id", "game_time"),)

    def __repr__(self) -> str:
        return f"<Game {self.away_team} @ {self.home_team} {self.game_time} ({self.status})>"


class Participant(db.Model):
    __tablename__ = "participants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False, unique=True)
    phone = db.Column(db.String(32))  # keep legacy; may be null
    telegram_chat_id = db.Column(db.String(64))  # new, nullable until user links
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
        db.Integer, db.ForeignKey("participants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    game_id = db.Column(
        db.Integer, db.ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    selected_team = db.Column(db.String(64), nullable=False)  # must match home/away team

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("participant_id", "game_id", name="uq_pick_participant_game"),
    )

    def __repr__(self) -> str:
        return f"<Pick p={self.participant_id} g={self.game_id} team={self.selected_team}>"


class Reminder(db.Model):
    """
    Lightweight log so jobs.py can track that a reminder/launch message was sent.
    Safe to keep even if you switch from SMS to Telegram; 'channel' covers both.
    """
    __tablename__ = "reminders"

    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(
        db.Integer, db.ForeignKey("weeks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    participant_id = db.Column(
        db.Integer, db.ForeignKey("participants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind = db.Column(db.String(32), nullable=False)      # e.g., 'launch', 'deadline', 'nudge'
    channel = db.Column(db.String(32), nullable=False, default="telegram")  # 'sms' | 'telegram' | 'email'
    message_sid = db.Column(db.String(128))              # SMS SID or Telegram message id (optional)
    status = db.Column(db.String(32), default="sent")    # 'sent', 'failed', etc.
    details = db.Column(db.Text)                         # error text or extra info
    sent_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    week = db.relationship("Week", lazy=True)
    participant = db.relationship("Participant", lazy=True)

    __table_args__ = (
        db.Index("ix_reminders_week_kind_part", "week_id", "kind", "participant_id"),
    )

    def __repr__(self) -> str:
        return f"<Reminder week={self.week_id} part={self.participant_id} {self.kind}/{self.channel}>"


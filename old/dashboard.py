import streamlit as st
from flask_app import create_app
from models import db, Participant, Game, Pick

app = create_app()
st.set_page_config(page_title="NFL Picks Dashboard", layout="wide")
st.title("🏈 NFL Picks Dashboard")

with app.app_context():
    participants = Participant.query.order_by(Participant.name).all()
    rows = []
    for p in participants:
        wins = db.session.query(db.func.count(Pick.id)).join(Game).filter(
            Pick.participant_id == p.id,
            Game.winner.isnot(None),
            Pick.selected_team == Game.winner,
        ).scalar() or 0
        losses = db.session.query(db.func.count(Pick.id)).join(Game).filter(
            Pick.participant_id == p.id,
            Game.winner.isnot(None),
            Pick.selected_team != Game.winner,
        ).scalar() or 0
        rows.append({"name": p.name, "wins": wins, "losses": losses})
    rows.sort(key=lambda r: r["wins"], reverse=True)
    st.subheader("📊 Current Standings")
    st.table(rows)

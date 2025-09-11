# dashboard.py
import streamlit as st
from app import create_app
from models import db, Participant, Game, Pick
from flask_app import create_app

# Initialize Flask app for DB access
app = create_app()

st.set_page_config(page_title="NFL Picks Dashboard", layout="wide")
st.title("🏈 NFL Picks Dashboard")

with app.app_context():
    participants = Participant.query.all()
    games = Game.query.all()

    # Compute standings
    standings = []
    for participant in participants:
        wins = 0
        losses = 0
        picks = Pick.query.filter_by(participant_id=participant.id).all()

        for pick in picks:
            game = Game.query.get(pick.game_id)
            if game and game.winner:
                if pick.selected_team == game.winner:
                    wins += 1
                else:
                    losses += 1

        standings.append({
            "name": participant.name,
            "wins": wins,
            "losses": losses,
        })

    # Sort standings by wins (descending)
    standings = sorted(standings, key=lambda x: x["wins"], reverse=True)

    # Display standings table
    st.subheader("📊 Current Standings")
    st.table(standings)

    # Debug info (optional)
    st.write("Total participants:", len(participants))
    st.write("Total games tracked:", len(games))


import streamlit as st
from supabase import create_client
import os
import uuid

# --- Supabase setup ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Authentication state ---
if "user" not in st.session_state:
    st.session_state.user = None

def login(email, password):
    try:
        response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        st.session_state.user = response.user
        st.success(f"Welcome {email}!")
    except Exception as e:
        st.error(f"Login failed: {e}")

def logout():
    st.session_state.user = None
    st.success("Logged out!")

# --- Save Functions ---
def save_journal_entry(text):
    supabase.table("journals").insert({
        "id": str(uuid.uuid4()),
        "user_id": st.session_state.user.id,
        "text": text
    }).execute()
    st.success("✅ Journal entry saved successfully!")

def save_goal(text):
    supabase.table("goals").insert({
        "id": str(uuid.uuid4()),
        "user_id": st.session_state.user.id,
        "text": text
    }).execute()
    st.success("🎯 Goal saved successfully!")

def save_calendar_block(text):
    supabase.table("calendar_blocks").insert({
        "id": str(uuid.uuid4()),
        "user_id": st.session_state.user.id,
        "text": text
    }).execute()
    st.success("📅 Calendar block saved successfully!")

# --- UI Pages ---
def show_login():
    st.title("✨ Manifestation Lab")
    st.subheader("🔑 Login")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        login(email, password)

def show_main():
    st.sidebar.success(f"Logged in as {st.session_state.user.email}")
    if st.sidebar.button("Logout"):
        logout()
        st.stop()

    st.sidebar.header("Choose an Action")
    choice = st.sidebar.radio("What do you want to do?", ["Journal Entry", "Set a Goal", "Calendar Block", "Review Progress"])

    # Journal Entry
    if choice == "Journal Entry":
        st.header("📝 Journal Entry")
        text = st.text_area("Write your thoughts", key="journal_input")
        if st.button("Save Journal"):
            if text.strip():
                save_journal_entry(text.strip())
                st.session_state["journal_input"] = ""  # safe reset

    # Set a Goal
    elif choice == "Set a Goal":
        st.header("🎯 Set a Goal")
        text = st.text_area("Define your goal", key="goal_input")
        if st.button("Save Goal"):
            if text.strip():
                save_goal(text.strip())
                st.session_state["goal_input"] = ""  # safe reset

    # Calendar Block
    elif choice == "Calendar Block":
        st.header("📅 Calendar Block")
        text = st.text_area("Describe your block", key="calendar_input")
        if st.button("Save Block"):
            if text.strip():
                save_calendar_block(text.strip())
                st.session_state["calendar_input"] = ""  # safe reset

    # Review Progress
    elif choice == "Review Progress":
        st.header("📊 Review Progress")
        st.info("Progress review logic is unchanged.")

# --- Main Entry Point ---
def main():
    if st.session_state.user:
        show_main()
    else:
        show_login()

if __name__ == "__main__":
    main()


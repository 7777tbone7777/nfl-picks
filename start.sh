#!/bin/bash
# Start Flask via gunicorn on port 8000
gunicorn wsgi:app --bind 0.0.0.0:8000 &

# Start Streamlit on port 8501
streamlit run dashboard.py --server.port 8501 --server.headless true --server.enableCORS false &

wait -n


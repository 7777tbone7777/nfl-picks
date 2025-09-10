#!/bin/bash
# Start FastAPI on port 8000
uvicorn main:app --host 0.0.0.0 --port 8000 &

# Start Streamlit on port 8501
streamlit run dashboard.py --server.port 8501 --server.headless true --server.enableCORS false &

# Keep dyno alive
wait -n

from flask_app import create_app
from jobs import calculate_and_send_results

if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        calculate_and_send_results()
        print("✅ Results job finished.")

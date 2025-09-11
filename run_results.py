from app import create_app
from jobs import calculate_and_send_results
from flask_app import create_app

if __name__ == '__main__':
    app = create_app()
    calculate_and_send_results(app)

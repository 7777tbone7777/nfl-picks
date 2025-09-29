# bot/cron_runner.py
import argparse
from .cron_jobs import cron_import_upcoming_week, cron_syncscores_latest_active
from .logging_setup import setup_logging

def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="Run NFL picks cron tasks")
    parser.add_argument("cmd", choices=["import_upcoming_week", "syncscores"], help="Which task to run")
    args = parser.parse_args()

    if args.cmd == "import_upcoming_week":
        res = cron_import_upcoming_week()
    else:
        res = cron_syncscores_latest_active()

    print(res)

if __name__ == "__main__":
    main()

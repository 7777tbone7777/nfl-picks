#!/usr/bin/env python3
"""
Import prop bets for Week 21 (Conference Championships).

Usage:
    python import_props.py              # Import props for Week 21 (latest season)
    python import_props.py 21 2025      # Import props for Week 21, season 2025
    python import_props.py --dry-run    # Show what would be imported
"""

import sys
from flask_app import create_app
from bot.jobs import import_props_from_csv

# Week 21 Conference Championship Props
# Format: game_label,description,option_a,option_b
WEEK_21_PROPS = """
AFC,Total points OVER/UNDER 47.5,OVER,UNDER
AFC,First team to score,BILLS,CHIEFS
AFC,Josh Allen 2+ passing TDs,YES,NO
AFC,Patrick Mahomes 250+ passing yards,YES,NO
AFC,Travis Kelce 50+ receiving yards,YES,NO
AFC,Will there be a defensive/special teams TD?,YES,NO
AFC,Winning margin OVER/UNDER 6.5,OVER,UNDER
AFC,Longest TD OVER/UNDER 35.5 yards,OVER,UNDER
AFC,Total sacks OVER/UNDER 4.5,OVER,UNDER
AFC,Will the game go to overtime?,YES,NO
AFC,James Cook 60+ rushing yards,YES,NO
AFC,Isiah Pacheco anytime TD scorer,YES,NO
AFC,First half OVER/UNDER 23.5,OVER,UNDER
AFC,4th quarter will be highest scoring quarter,YES,NO
AFC,Both teams score 20+ points,YES,NO
NFC,Total points OVER/UNDER 49.5,OVER,UNDER
NFC,First team to score,COMMANDERS,EAGLES
NFC,Jalen Hurts 2+ TDs (passing+rushing),YES,NO
NFC,Jayden Daniels 250+ passing yards,YES,NO
NFC,Saquon Barkley 100+ rushing yards,YES,NO
NFC,A.J. Brown 75+ receiving yards,YES,NO
NFC,Will there be a turnover in the first half?,YES,NO
NFC,Winning margin OVER/UNDER 7.5,OVER,UNDER
NFC,Terry McLaurin 50+ receiving yards,YES,NO
NFC,Total TDs OVER/UNDER 5.5,OVER,UNDER
NFC,Either team scores 30+ points,YES,NO
NFC,First half OVER/UNDER 24.5,OVER,UNDER
NFC,Both QBs throw for 200+ yards,YES,NO
NFC,Will there be a successful 2-point conversion?,YES,NO
NFC,Game decided by 3 points or less,YES,NO
"""


def main():
    args = sys.argv[1:]

    # Parse arguments
    dry_run = "--dry-run" in args
    args = [a for a in args if not a.startswith("--")]

    week = int(args[0]) if len(args) >= 1 else 21
    season_year = int(args[1]) if len(args) >= 2 else None

    if dry_run:
        print(f"DRY RUN: Would import props for Week {week}")
        print("\nProps to import:")
        for line in WEEK_21_PROPS.strip().split("\n"):
            if line.strip() and not line.startswith("#"):
                print(f"  {line}")
        return

    app = create_app()
    with app.app_context():
        result = import_props_from_csv(week, WEEK_21_PROPS, season_year)
        print(f"Import result: {result}")


if __name__ == "__main__":
    main()

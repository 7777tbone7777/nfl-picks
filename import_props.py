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
AFC,Jarrett Stidham (DEN): Passing Yards (Line: 215.5),OVER,UNDER
AFC,Drake Maye (NE): Total Passing TDs (Line: 1.5),OVER,UNDER
AFC,RJ Harvey (DEN): Rushing + Receiving Yards (Line: 65.5),OVER,UNDER
AFC,Stefon Diggs (NE): Total Receiving Yards (Line: 60.5),OVER,UNDER
AFC,Hunter Henry (NE): Total Receptions (Line: 3.5),OVER,UNDER
AFC,Courtland Sutton (DEN): Total Receptions (Line: 4.5),OVER,UNDER
AFC,Robert Spillane (NE): Total Tackles (Line: 7.5),OVER,UNDER
AFC,Longest Field Goal Made: Longer than 47.5 yards?,YES,NO
AFC,Broncos Defense: Total Team Sacks (Line: 2.5),OVER,UNDER
AFC,Defensive/Special Teams TD: Will there be one?,YES,NO
AFC,TreVeyon Henderson (NE): Anytime Touchdown,YES,NO
AFC,First Score of Game: Will it be a Touchdown?,YES,NO
AFC,Total Interceptions: Over/Under 1.5 (Both Teams),OVER,UNDER
AFC,Largest Lead of Game: Over/Under 10.5 Points,OVER,UNDER
AFC,Shortest TD Scored: Under 1.5 Yards (Goal line plunge)?,YES,NO
NFC,Matthew Stafford (LAR): Passing Yards (Line: 265.5),OVER,UNDER
NFC,Sam Darnold (SEA): Passing Yards (Line: 235.5),OVER,UNDER
NFC,Kyren Williams (LAR): Total Rushing Yards (Line: 75.5),OVER,UNDER
NFC,Kenneth Walker III (SEA): Rushing + Receiving Yards (Line: 85.5),OVER,UNDER
NFC,Puka Nacua (LAR): Total Receptions (Line: 6.5),OVER,UNDER
NFC,Jaxon Smith-Njigba (SEA): Total Receptions (Line: 5.5),OVER,UNDER
NFC,Sam Darnold (SEA): Will he score a Rushing TD?,YES,NO
NFC,Total 4th Down Conversions: Over/Under 1.5,OVER,UNDER
NFC,Will the Game go to Overtime?,YES,NO
NFC,Total Made Field Goals (Both Teams): (Line: 3.5),OVER,UNDER
NFC,Cooper Kupp (LAR): Longest Reception Over 22.5 Yards?,YES,NO
NFC,Team with Most Penalties: Rams or Seahawks?,RAMS,SEA
NFC,Total Sacks (Both Teams): Over/Under 5.5,OVER,UNDER
NFC,Opening Kickoff: Will it be a Touchback?,YES,NO
NFC,Final Play of Game: Will the QB take a knee?,YES,NO
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

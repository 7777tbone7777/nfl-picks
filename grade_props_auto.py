#!/usr/bin/env python3
"""
Auto-grade prop bets by fetching stats from ESPN.

Usage:
    python grade_props_auto.py 21              # Grade Week 21 props (dry run)
    python grade_props_auto.py 21 --commit     # Grade and save to DB
    python grade_props_auto.py 21 --verbose    # Show detailed parsing
"""

import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from typing import Optional

# ESPN API endpoints
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event_id}"


@dataclass
class GameData:
    """Parsed ESPN game data."""
    event_id: str
    home_team: str
    away_team: str
    home_abbrev: str
    away_abbrev: str
    status: str
    players: dict  # {player_name_lower: {stat_type: {stat_name: value}}}
    team_stats: dict  # {team_abbrev: {stat_name: value}}
    scoring_plays: list
    drives: list
    kicking: dict  # {team_abbrev: {longest_fg: int, made_fgs: int}}


def fetch_json(url: str) -> dict:
    """Fetch JSON from URL."""
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def find_games_for_week(week: int, season_year: int = 2025) -> list[dict]:
    """Find ESPN game IDs for a given week."""

    def parse_events(events):
        result = []
        for e in events:
            try:
                comps = e["competitions"][0]["competitors"]
                home = next((c for c in comps if c["homeAway"] == "home"), comps[0])
                away = next((c for c in comps if c["homeAway"] == "away"), comps[1])
                result.append({
                    "id": e["id"],
                    "name": e["name"],
                    "status": e["status"]["type"]["name"],
                    "home": home["team"]["displayName"],
                    "away": away["team"]["displayName"],
                    "home_abbrev": home["team"]["abbreviation"],
                    "away_abbrev": away["team"]["abbreviation"],
                })
            except Exception:
                continue
        return result

    # First, try current scoreboard (no week filter) - shows recent/current games
    try:
        data = fetch_json(ESPN_SCOREBOARD)
        events = data.get("events", [])
        if events:
            print(f"   Found {len(events)} games on current scoreboard")
            return parse_events(events)
    except Exception as e:
        print(f"   Warning: Current scoreboard failed: {e}")

    # Try postseason weeks 1-4 (Wild Card, Divisional, Conference, Super Bowl)
    for espn_week in [3, 2, 4, 1]:  # Conference=3, Divisional=2, etc.
        url = f"{ESPN_SCOREBOARD}?seasontype=3&week={espn_week}&year={season_year}"
        try:
            data = fetch_json(url)
            events = data.get("events", [])
            if events:
                print(f"   Found {len(events)} games at postseason week {espn_week}")
                return parse_events(events)
        except Exception as e:
            print(f"   Warning: Postseason week {espn_week} failed: {e}")

    # Fallback: try regular season week
    for season_type in [2, 3]:
        url = f"{ESPN_SCOREBOARD}?seasontype={season_type}&week={week}&year={season_year}"
        try:
            data = fetch_json(url)
            events = data.get("events", [])
            if events:
                return parse_events(events)
        except Exception:
            pass

    return []


def fetch_game_data(event_id: str) -> GameData:
    """Fetch and parse all relevant stats from an ESPN game summary."""
    url = ESPN_SUMMARY.format(event_id=event_id)
    data = fetch_json(url)

    boxscore = data.get("boxscore", {})
    header = data.get("header", {})

    # Get team info
    competitions = header.get("competitions", [{}])[0]
    competitors = competitions.get("competitors", [])
    home_team = away_team = home_abbrev = away_abbrev = ""
    for c in competitors:
        team = c.get("team", {})
        if c.get("homeAway") == "home":
            home_team = team.get("displayName", "")
            home_abbrev = team.get("abbreviation", "")
        else:
            away_team = team.get("displayName", "")
            away_abbrev = team.get("abbreviation", "")

    status = competitions.get("status", {}).get("type", {}).get("name", "")

    # Parse player stats
    players = {}
    kicking = {}

    for team_data in boxscore.get("players", []):
        team_abbrev = team_data.get("team", {}).get("abbreviation", "")

        for stat_group in team_data.get("statistics", []):
            stat_type = stat_group.get("name", "")
            labels = stat_group.get("labels", [])

            for athlete in stat_group.get("athletes", []):
                name = athlete.get("athlete", {}).get("displayName", "")
                name_lower = name.lower()
                stats = athlete.get("stats", [])
                stat_dict = dict(zip(labels, stats))

                if name_lower not in players:
                    players[name_lower] = {"team": team_abbrev}

                players[name_lower][stat_type] = stat_dict

                # Special handling for kicking
                if stat_type == "kicking":
                    if team_abbrev not in kicking:
                        kicking[team_abbrev] = {"longest_fg": 0, "made_fgs": 0}
                    try:
                        long_fg = int(stat_dict.get("LONG", 0))
                        fg_made = int(stat_dict.get("FG", "0/0").split("/")[0])
                        kicking[team_abbrev]["longest_fg"] = max(kicking[team_abbrev]["longest_fg"], long_fg)
                        kicking[team_abbrev]["made_fgs"] += fg_made
                    except:
                        pass

    # Parse team stats
    team_stats = {}
    for team_data in boxscore.get("teams", []):
        abbrev = team_data.get("team", {}).get("abbreviation", "")
        team_stats[abbrev] = {}
        for stat in team_data.get("statistics", []):
            label = stat.get("label", "")
            value = stat.get("displayValue", "")
            team_stats[abbrev][label] = value

    # Scoring plays
    scoring_plays = data.get("scoringPlays", [])

    # Drives (for first/last play)
    drives = data.get("drives", {}).get("previous", [])

    return GameData(
        event_id=event_id,
        home_team=home_team,
        away_team=away_team,
        home_abbrev=home_abbrev,
        away_abbrev=away_abbrev,
        status=status,
        players=players,
        team_stats=team_stats,
        scoring_plays=scoring_plays,
        drives=drives,
        kicking=kicking,
    )


def parse_player_name(description: str) -> Optional[str]:
    """Extract player name from prop description."""
    # Pattern: "Player Name (TEAM): ..."
    match = re.match(r"^([^(]+)\s*\([A-Z]{2,3}\)", description)
    if match:
        return match.group(1).strip().lower()
    return None


def parse_line(description: str) -> Optional[float]:
    """Extract the line/threshold from prop description."""
    # Pattern: "Line: 215.5" or "Over/Under 1.5" or "> 47.5"
    match = re.search(r"(?:Line:\s*|Over/Under\s*|>\s*|Under\s*)(\d+\.?\d*)", description)
    if match:
        return float(match.group(1))
    return None


def get_player_stat(game: GameData, player_name: str, stat_type: str, stat_key: str) -> Optional[float]:
    """Get a specific stat for a player."""
    player = game.players.get(player_name.lower())
    if not player:
        # Try fuzzy match
        for pname, pdata in game.players.items():
            if player_name.lower() in pname or pname in player_name.lower():
                player = pdata
                break

    if not player:
        return None

    stat_group = player.get(stat_type, {})
    value = stat_group.get(stat_key)
    if value is None:
        return None

    try:
        # Handle "5-21" format for sacks
        if "-" in str(value) and stat_key != "C/ATT":
            return float(str(value).split("-")[0])
        return float(value)
    except:
        return None


def grade_prop(prop: dict, afc_game: GameData, nfc_game: GameData, verbose: bool = False) -> Optional[str]:
    """
    Grade a single prop bet.
    Returns the winning option (e.g., "OVER", "UNDER", "YES", "NO", team name) or None if can't grade.
    """
    desc = prop["description"]
    option_a = prop["option_a"].upper()
    option_b = prop["option_b"].upper()
    game_label = prop.get("game_label", "").upper()

    game = afc_game if game_label == "AFC" else nfc_game

    if verbose:
        print(f"\n  Grading: {desc}")
        print(f"  Options: {option_a} / {option_b}")

    # === PLAYER PASSING YARDS ===
    if "Passing Yards" in desc and "Line:" in desc:
        player = parse_player_name(desc)
        line = parse_line(desc)
        if player and line is not None:
            yards = get_player_stat(game, player, "passing", "YDS")
            if verbose:
                print(f"  -> Player: {player}, Line: {line}, Actual: {yards}")
            if yards is not None:
                return option_a if yards > line else option_b  # OVER if > line, else UNDER

    # === PLAYER PASSING TDs ===
    if "Passing TDs" in desc and "Line:" in desc:
        player = parse_player_name(desc)
        line = parse_line(desc)
        if player and line is not None:
            tds = get_player_stat(game, player, "passing", "TD")
            if verbose:
                print(f"  -> Player: {player}, Line: {line}, Actual: {tds}")
            if tds is not None:
                return option_a if tds > line else option_b

    # === PLAYER RUSHING + RECEIVING YARDS ===
    if "Rushing + Receiving Yards" in desc and "Line:" in desc:
        player = parse_player_name(desc)
        line = parse_line(desc)
        if player and line is not None:
            rush = get_player_stat(game, player, "rushing", "YDS") or 0
            rec = get_player_stat(game, player, "receiving", "YDS") or 0
            total = rush + rec
            if verbose:
                print(f"  -> Player: {player}, Line: {line}, Rush: {rush}, Rec: {rec}, Total: {total}")
            return option_a if total > line else option_b

    # === PLAYER RUSHING YARDS ===
    if "Rushing Yards" in desc and "Line:" in desc and "Receiving" not in desc:
        player = parse_player_name(desc)
        line = parse_line(desc)
        if player and line is not None:
            yards = get_player_stat(game, player, "rushing", "YDS")
            if verbose:
                print(f"  -> Player: {player}, Line: {line}, Actual: {yards}")
            if yards is not None:
                return option_a if yards > line else option_b

    # === PLAYER RECEIVING YARDS ===
    if "Receiving Yards" in desc and "Line:" in desc:
        player = parse_player_name(desc)
        line = parse_line(desc)
        if player and line is not None:
            yards = get_player_stat(game, player, "receiving", "YDS")
            if verbose:
                print(f"  -> Player: {player}, Line: {line}, Actual: {yards}")
            if yards is not None:
                return option_a if yards > line else option_b

    # === PLAYER RECEPTIONS ===
    if "Receptions" in desc and "Line:" in desc:
        player = parse_player_name(desc)
        line = parse_line(desc)
        if player and line is not None:
            recs = get_player_stat(game, player, "receiving", "REC")
            if verbose:
                print(f"  -> Player: {player}, Line: {line}, Actual: {recs}")
            if recs is not None:
                return option_a if recs > line else option_b

    # === PLAYER TACKLES ===
    if "Tackles" in desc and "Line:" in desc:
        player = parse_player_name(desc)
        line = parse_line(desc)
        if player and line is not None:
            tackles = get_player_stat(game, player, "defensive", "TOT")
            if verbose:
                print(f"  -> Player: {player}, Line: {line}, Actual: {tackles}")
            if tackles is not None:
                return option_a if tackles > line else option_b

    # === LONGEST FIELD GOAL ===
    if "Longest Field Goal" in desc and "47.5" in desc:
        longest = max(
            game.kicking.get(game.home_abbrev, {}).get("longest_fg", 0),
            game.kicking.get(game.away_abbrev, {}).get("longest_fg", 0)
        )
        if verbose:
            print(f"  -> Longest FG: {longest}")
        return option_a if longest > 47.5 else option_b  # YES if > 47.5, NO otherwise

    # === TEAM SACKS (e.g., "Broncos Defense: Total Team Sacks") ===
    if "Defense" in desc and "Sacks" in desc and "Line:" in desc:
        line = parse_line(desc)
        # Figure out which team's defense
        if "Broncos" in desc or "DEN" in desc:
            team = "DEN"
        elif "Patriots" in desc or "NE" in desc:
            team = "NE"
        elif "Rams" in desc or "LAR" in desc or "LA" in desc:
            team = "LAR"
        elif "Seahawks" in desc or "SEA" in desc:
            team = "SEA"
        else:
            team = None

        if team and line is not None:
            # Sacks BY this team = sacks AGAINST the opponent
            # Get from opponent's passing stats "SACKS" field
            opp = game.away_abbrev if team == game.home_abbrev else game.home_abbrev

            # Find opponent QB's sacks taken
            for pname, pdata in game.players.items():
                if pdata.get("team") == opp and "passing" in pdata:
                    sacks_str = pdata["passing"].get("SACKS", "0-0")
                    try:
                        sacks = int(sacks_str.split("-")[0])
                        if verbose:
                            print(f"  -> {team} defense sacks (from {opp} QB): {sacks}, Line: {line}")
                        return option_a if sacks > line else option_b
                    except:
                        pass

    # === DEFENSIVE/SPECIAL TEAMS TD ===
    if "Defensive" in desc and "Special Teams" in desc and "TD" in desc:
        has_dst_td = False
        for play in game.scoring_plays:
            ptype = play.get("type", {}).get("text", "").lower()
            if "interception" in ptype or "fumble" in ptype or "punt" in ptype or "kick" in ptype:
                has_dst_td = True
                break
        if verbose:
            print(f"  -> D/ST TD found: {has_dst_td}")
        return option_a if has_dst_td else option_b  # YES if found, NO otherwise

    # === ANYTIME TOUCHDOWN ===
    if "Anytime Touchdown" in desc:
        player = parse_player_name(desc)
        if player:
            # Check rushing and receiving TDs
            rush_td = get_player_stat(game, player, "rushing", "TD") or 0
            rec_td = get_player_stat(game, player, "receiving", "TD") or 0
            total_td = rush_td + rec_td
            if verbose:
                print(f"  -> Player: {player}, Rush TD: {rush_td}, Rec TD: {rec_td}")
            return option_a if total_td > 0 else option_b  # YES if scored, NO otherwise

    # === RUSHING TD (specific player) ===
    if "Rushing TD" in desc and "Will he score" in desc:
        player = parse_player_name(desc)
        if player:
            rush_td = get_player_stat(game, player, "rushing", "TD") or 0
            if verbose:
                print(f"  -> Player: {player}, Rush TD: {rush_td}")
            return option_a if rush_td > 0 else option_b

    # === FIRST SCORE TOUCHDOWN ===
    if "First Score" in desc and "Touchdown" in desc:
        if game.scoring_plays:
            first = game.scoring_plays[0]
            ptype = first.get("type", {}).get("text", "").lower()
            is_td = "touchdown" in ptype
            if verbose:
                print(f"  -> First score type: {ptype}, is TD: {is_td}")
            return option_a if is_td else option_b

    # === TOTAL INTERCEPTIONS ===
    if "Total Interceptions" in desc and "Line:" in desc:
        line = parse_line(desc)
        if line is not None:
            total_ints = 0
            for pname, pdata in game.players.items():
                if "passing" in pdata:
                    try:
                        ints = int(pdata["passing"].get("INT", 0))
                        total_ints += ints
                    except:
                        pass
            if verbose:
                print(f"  -> Total INTs: {total_ints}, Line: {line}")
            return option_a if total_ints > line else option_b

    # === LARGEST LEAD ===
    if "Largest Lead" in desc and "Line:" in desc:
        line = parse_line(desc)
        if line is not None:
            max_lead = 0
            for play in game.scoring_plays:
                home = play.get("homeScore", 0)
                away = play.get("awayScore", 0)
                lead = abs(home - away)
                max_lead = max(max_lead, lead)
            if verbose:
                print(f"  -> Largest lead: {max_lead}, Line: {line}")
            return option_a if max_lead > line else option_b

    # === SHORTEST TD ===
    if "Shortest TD" in desc and "1.5" in desc:
        shortest = 999
        for play in game.scoring_plays:
            text = play.get("text", "")
            ptype = play.get("type", {}).get("text", "").lower()
            if "touchdown" in ptype:
                # Parse yards from text like "6 Yd Rush" or "2 Yd pass"
                match = re.search(r"(\d+)\s*Yd", text)
                if match:
                    yards = int(match.group(1))
                    shortest = min(shortest, yards)
        if verbose:
            print(f"  -> Shortest TD: {shortest} yards")
        return option_a if shortest < 1.5 else option_b  # YES if < 1.5, NO otherwise

    # === 4TH DOWN CONVERSIONS ===
    if "4th Down Conversions" in desc and "Line:" in desc:
        line = parse_line(desc)
        if line is not None:
            total = 0
            for abbrev, stats in game.team_stats.items():
                eff = stats.get("4th down efficiency", "0-0")
                try:
                    made = int(eff.split("-")[0])
                    total += made
                except:
                    pass
            if verbose:
                print(f"  -> 4th down conversions: {total}, Line: {line}")
            return option_a if total > line else option_b

    # === OVERTIME ===
    if "Overtime" in desc:
        # Check if game went to OT by looking at period in last scoring play or drives
        went_to_ot = False
        for play in game.scoring_plays:
            period = play.get("period", {}).get("number", 0)
            if period > 4:
                went_to_ot = True
                break
        if verbose:
            print(f"  -> Went to OT: {went_to_ot}")
        return option_a if went_to_ot else option_b

    # === TOTAL FIELD GOALS ===
    if "Total Made Field Goals" in desc and "Line:" in desc:
        line = parse_line(desc)
        if line is not None:
            total = sum(k.get("made_fgs", 0) for k in game.kicking.values())
            if verbose:
                print(f"  -> Total FGs: {total}, Line: {line}")
            return option_a if total > line else option_b

    # === LONGEST RECEPTION ===
    if "Longest Reception" in desc:
        player = parse_player_name(desc)
        line = parse_line(desc)
        if player and line is not None:
            longest = get_player_stat(game, player, "receiving", "LONG")
            if verbose:
                print(f"  -> Player: {player}, Longest rec: {longest}, Line: {line}")
            if longest is not None:
                return option_a if longest > line else option_b

    # === MOST PENALTIES ===
    if "Most Penalties" in desc:
        home_pen = game.team_stats.get(game.home_abbrev, {}).get("Penalties", "0-0")
        away_pen = game.team_stats.get(game.away_abbrev, {}).get("Penalties", "0-0")
        try:
            home_count = int(home_pen.split("-")[0])
            away_count = int(away_pen.split("-")[0])
            if verbose:
                print(f"  -> {game.home_abbrev}: {home_count}, {game.away_abbrev}: {away_count}")
            if home_count > away_count:
                # Return whichever option matches home team
                return option_a if game.home_abbrev in option_a else option_b
            elif away_count > home_count:
                return option_a if game.away_abbrev in option_a else option_b
            else:
                return None  # Tie
        except:
            pass

    # === TOTAL SACKS (both teams) ===
    if "Total Sacks" in desc and "Both Teams" in desc and "Line:" in desc:
        line = parse_line(desc)
        if line is not None:
            total_sacks = 0
            for pname, pdata in game.players.items():
                if "passing" in pdata:
                    sacks_str = pdata["passing"].get("SACKS", "0-0")
                    try:
                        sacks = int(sacks_str.split("-")[0])
                        total_sacks += sacks
                    except:
                        pass
            if verbose:
                print(f"  -> Total sacks (both teams): {total_sacks}, Line: {line}")
            return option_a if total_sacks > line else option_b

    # === OPENING KICKOFF TOUCHBACK ===
    if "Opening Kickoff" in desc and "Touchback" in desc:
        if game.drives:
            first_drive = game.drives[0]
            plays = first_drive.get("plays", [])
            if plays:
                first_text = plays[0].get("text", "").lower()
                is_touchback = "touchback" in first_text
                if verbose:
                    print(f"  -> First play: {first_text[:80]}")
                    print(f"  -> Touchback: {is_touchback}")
                return option_a if is_touchback else option_b

    # === FINAL PLAY KNEE ===
    if "Final Play" in desc and "knee" in desc.lower():
        if game.drives:
            last_drive = game.drives[-1]
            plays = last_drive.get("plays", [])
            # Find last non-administrative play
            for play in reversed(plays):
                text = play.get("text", "").lower()
                ptype = play.get("type", {}).get("text", "").lower()
                if "end" in ptype or "timeout" in ptype:
                    continue
                is_knee = "kneel" in text or "knee" in text
                if verbose:
                    print(f"  -> Last play: {text[:80]}")
                    print(f"  -> Is knee: {is_knee}")
                return option_a if is_knee else option_b

    if verbose:
        print(f"  -> COULD NOT PARSE")
    return None


def main():
    args = sys.argv[1:]

    if not args or not args[0].isdigit():
        print("Usage: python grade_props_auto.py <week> [--commit] [--verbose]")
        print("  --commit   Save results to database")
        print("  --verbose  Show detailed parsing")
        return

    week = int(args[0])
    commit = "--commit" in args
    verbose = "--verbose" in args

    print(f"=== Auto-grading props for Week {week} ===\n")

    # Find games
    print("1. Finding games...")
    games = find_games_for_week(week)
    if not games:
        print("ERROR: No games found for this week")
        return

    for g in games:
        print(f"   {g['away']} @ {g['home']} ({g['status']})")

    # Check all games are final
    not_final = [g for g in games if g["status"] != "STATUS_FINAL"]
    if not_final:
        print(f"\nERROR: {len(not_final)} game(s) not final yet:")
        for g in not_final:
            print(f"   {g['name']} - {g['status']}")
        return

    # Fetch game data
    print("\n2. Fetching game data from ESPN...")
    game_data = {}
    for g in games:
        print(f"   Fetching {g['away']} @ {g['home']}...")
        data = fetch_game_data(g["id"])
        # Determine AFC/NFC based on teams
        if any(t in g["name"] for t in ["Patriots", "Broncos", "Chiefs", "Bills", "Ravens", "Bengals", "Dolphins", "Jets", "Steelers", "Browns", "Colts", "Titans", "Jaguars", "Texans", "Raiders", "Chargers"]):
            game_data["AFC"] = data
        else:
            game_data["NFC"] = data

    if "AFC" not in game_data or "NFC" not in game_data:
        print("Warning: Could not identify AFC/NFC games clearly")
        # Assign based on order
        if len(games) >= 2:
            game_data["AFC"] = fetch_game_data(games[0]["id"])
            game_data["NFC"] = fetch_game_data(games[1]["id"])

    print(f"   AFC: {game_data.get('AFC', {}).away_team} @ {game_data.get('AFC', {}).home_team}")
    print(f"   NFC: {game_data.get('NFC', {}).away_team} @ {game_data.get('NFC', {}).home_team}")

    # Load props from database
    print("\n3. Loading props from database...")
    from flask_app import create_app
    from models import db, PropBet
    from sqlalchemy import text as T

    app = create_app()
    with app.app_context():
        season_year = db.session.execute(T("SELECT MAX(season_year) FROM weeks")).scalar()
        week_id = db.session.execute(
            T("SELECT id FROM weeks WHERE season_year=:y AND week_number=:w"),
            {"y": season_year, "w": week},
        ).scalar()

        if not week_id:
            print(f"ERROR: Week {week} not found in database")
            return

        props = PropBet.query.filter_by(week_id=week_id).order_by(PropBet.id).all()
        print(f"   Found {len(props)} props")

        # Grade each prop
        print("\n4. Grading props...")
        results = []
        graded = 0
        failed = 0

        for prop in props:
            prop_dict = {
                "id": prop.id,
                "description": prop.description,
                "option_a": prop.option_a,
                "option_b": prop.option_b,
                "game_label": prop.game_label,
            }

            result = grade_prop(
                prop_dict,
                game_data.get("AFC"),
                game_data.get("NFC"),
                verbose=verbose
            )

            results.append({
                "id": prop.id,
                "game": prop.game_label,
                "desc": prop.description[:50],
                "result": result,
            })

            if result:
                graded += 1
                if not verbose:
                    print(f"   #{prop.id} {prop.game_label}: {result} <- {prop.description[:40]}...")
            else:
                failed += 1
                print(f"   #{prop.id} {prop.game_label}: FAILED <- {prop.description[:40]}...")

        print(f"\n5. Summary: {graded} graded, {failed} failed")

        if commit and graded > 0:
            print("\n6. Saving to database...")
            for r in results:
                if r["result"]:
                    db.session.execute(
                        T("UPDATE prop_bets SET result=:r WHERE id=:id"),
                        {"r": r["result"], "id": r["id"]},
                    )
            db.session.commit()
            print(f"   Saved {graded} results")
        elif not commit:
            print("\n   (Dry run - use --commit to save)")


if __name__ == "__main__":
    main()

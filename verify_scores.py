#!/usr/bin/env python3
"""
Verify scoreboard data - run with: heroku run python verify_scores.py
"""
import os
from sqlalchemy import create_engine, text

# Get database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI")
if not DATABASE_URL:
    print("ERROR: No DATABASE_URL found in environment")
    exit(1)

# Fix Heroku's postgres:// to postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgres://"):]

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    # 1. Get current season
    season = conn.execute(text("SELECT MAX(season_year) FROM weeks")).scalar()
    print(f"\n{'='*60}")
    print(f"SEASON: {season}")
    print(f"{'='*60}\n")

    # 2. Check games with NULL vs populated winner field
    print("--- GAMES: winner field status ---")
    winner_stats = conn.execute(text("""
        SELECT
            COUNT(*) AS total_games,
            COUNT(winner) AS has_winner,
            COUNT(*) - COUNT(winner) AS null_winner,
            COUNT(CASE WHEN LOWER(status) = 'final' THEN 1 END) AS final_games,
            COUNT(CASE WHEN LOWER(status) = 'final' AND winner IS NULL THEN 1 END) AS final_but_no_winner
        FROM games g
        JOIN weeks w ON w.id = g.week_id
        WHERE w.season_year = :y
    """), {"y": season}).mappings().first()

    print(f"Total games:                {winner_stats['total_games']}")
    print(f"Games with winner set:      {winner_stats['has_winner']}")
    print(f"Games with winner NULL:     {winner_stats['null_winner']}")
    print(f"FINAL games:                {winner_stats['final_games']}")
    print(f"FINAL but winner is NULL:   {winner_stats['final_but_no_winner']} <-- PROBLEM if > 0")

    # 3. Show FINAL games missing winner field
    if winner_stats['final_but_no_winner'] > 0:
        print(f"\n--- FINAL games with NULL winner (first 20) ---")
        missing = conn.execute(text("""
            SELECT w.week_number, g.id, g.away_team, g.home_team,
                   g.away_score, g.home_score, g.winner, g.favorite_team, g.spread_pts
            FROM games g
            JOIN weeks w ON w.id = g.week_id
            WHERE w.season_year = :y
              AND LOWER(g.status) = 'final'
              AND g.winner IS NULL
            ORDER BY w.week_number, g.id
            LIMIT 20
        """), {"y": season}).mappings().all()

        for g in missing:
            spread_info = f"({g['favorite_team']} -{g['spread_pts']})" if g['favorite_team'] else "(no spread)"
            print(f"  W{g['week_number']:>2} | {g['away_team']} {g['away_score']} @ {g['home_team']} {g['home_score']} | winner=NULL | {spread_info}")

    # 4. Calculate scores THREE ways for comparison
    print(f"\n{'='*60}")
    print("SCOREBOARD COMPARISON - 3 Methods")
    print(f"{'='*60}\n")

    # Get participant names
    participants = dict(conn.execute(text("SELECT id, name FROM participants")).fetchall())

    # Method 1: Using stored g.winner field (what /seasonboard does)
    print("Method 1: Using stored 'winner' field (current /seasonboard)")
    method1 = conn.execute(text("""
        SELECT p.participant_id, COUNT(*) as wins
        FROM picks p
        JOIN games g ON g.id = p.game_id
        JOIN weeks w ON w.id = g.week_id
        WHERE w.season_year = :y
          AND LOWER(COALESCE(g.status,'')) = 'final'
          AND g.winner IS NOT NULL
          AND LOWER(TRIM(p.selected_team)) = LOWER(TRIM(g.winner))
        GROUP BY p.participant_id
        ORDER BY wins DESC
    """), {"y": season}).mappings().all()

    scores1 = {r['participant_id']: r['wins'] for r in method1}
    for pid, wins in sorted(scores1.items(), key=lambda x: -x[1]):
        print(f"  {participants.get(pid, pid):<12}: {wins}")

    # Method 2: Straight-up winner (actual game winner, ignoring spread)
    print("\nMethod 2: Straight-up winner (ignore spread)")
    method2 = conn.execute(text("""
        SELECT p.participant_id, COUNT(*) as wins
        FROM picks p
        JOIN games g ON g.id = p.game_id
        JOIN weeks w ON w.id = g.week_id
        WHERE w.season_year = :y
          AND LOWER(COALESCE(g.status,'')) = 'final'
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
          AND (
              (g.home_score > g.away_score AND LOWER(TRIM(p.selected_team)) = LOWER(TRIM(g.home_team)))
              OR
              (g.away_score > g.home_score AND LOWER(TRIM(p.selected_team)) = LOWER(TRIM(g.away_team)))
          )
        GROUP BY p.participant_id
        ORDER BY wins DESC
    """), {"y": season}).mappings().all()

    scores2 = {r['participant_id']: r['wins'] for r in method2}
    for pid, wins in sorted(scores2.items(), key=lambda x: -x[1]):
        print(f"  {participants.get(pid, pid):<12}: {wins}")

    # Method 3: Calculate ATS winner on-the-fly
    print("\nMethod 3: ATS winner (calculated on-the-fly)")

    # Get all FINAL games with scores and spreads
    games_data = conn.execute(text("""
        SELECT g.id, g.home_team, g.away_team, g.home_score, g.away_score,
               g.favorite_team, g.spread_pts
        FROM games g
        JOIN weeks w ON w.id = g.week_id
        WHERE w.season_year = :y
          AND LOWER(COALESCE(g.status,'')) = 'final'
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
    """), {"y": season}).mappings().all()

    def calc_ats_winner(home_team, away_team, home_score, away_score, favorite_team, spread_pts):
        """Calculate ATS winner. Returns team name or None for push."""
        if home_score is None or away_score is None:
            return None
        if not favorite_team or spread_pts is None:
            # No spread - use straight-up winner
            if home_score > away_score:
                return home_team
            elif away_score > home_score:
                return away_team
            return None  # tie

        spread = float(spread_pts)
        # Adjust score based on spread
        if favorite_team.lower().strip() == home_team.lower().strip():
            adjusted_home = home_score - spread
            adjusted_away = away_score
        elif favorite_team.lower().strip() == away_team.lower().strip():
            adjusted_home = home_score
            adjusted_away = away_score - spread
        else:
            # Unknown favorite - straight up
            if home_score > away_score:
                return home_team
            elif away_score > home_score:
                return away_team
            return None

        if adjusted_home > adjusted_away:
            return home_team
        elif adjusted_away > adjusted_home:
            return away_team
        return None  # push

    # Build dict of game_id -> ATS winner
    ats_winners = {}
    for g in games_data:
        ats_winners[g['id']] = calc_ats_winner(
            g['home_team'], g['away_team'],
            g['home_score'], g['away_score'],
            g['favorite_team'], g['spread_pts']
        )

    # Get all picks for FINAL games
    picks_data = conn.execute(text("""
        SELECT p.participant_id, p.game_id, p.selected_team
        FROM picks p
        JOIN games g ON g.id = p.game_id
        JOIN weeks w ON w.id = g.week_id
        WHERE w.season_year = :y
          AND LOWER(COALESCE(g.status,'')) = 'final'
          AND p.selected_team IS NOT NULL
    """), {"y": season}).mappings().all()

    scores3 = {}
    for p in picks_data:
        gid = p['game_id']
        ats_winner = ats_winners.get(gid)
        if ats_winner and p['selected_team'].strip().lower() == ats_winner.strip().lower():
            scores3[p['participant_id']] = scores3.get(p['participant_id'], 0) + 1

    for pid, wins in sorted(scores3.items(), key=lambda x: -x[1]):
        print(f"  {participants.get(pid, pid):<12}: {wins}")

    # Summary comparison
    print(f"\n{'='*60}")
    print("SUMMARY COMPARISON")
    print(f"{'='*60}")
    print(f"{'Name':<12} | {'Stored':>8} | {'Straight':>8} | {'ATS Calc':>8}")
    print("-" * 50)

    all_pids = set(scores1.keys()) | set(scores2.keys()) | set(scores3.keys())
    for pid in sorted(all_pids, key=lambda x: -scores3.get(x, 0)):
        name = participants.get(pid, str(pid))
        s1 = scores1.get(pid, 0)
        s2 = scores2.get(pid, 0)
        s3 = scores3.get(pid, 0)
        flag = " <-- MISMATCH" if s1 != s3 else ""
        print(f"{name:<12} | {s1:>8} | {s2:>8} | {s3:>8}{flag}")

    print(f"\nStored = what /seasonboard shows (uses g.winner column)")
    print(f"Straight = straight-up wins (actual game winner)")
    print(f"ATS Calc = against-the-spread calculated on-the-fly")
    print()

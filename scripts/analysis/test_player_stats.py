"""
test_player_stats.py
=====================
Picks 5 random players from matches.csv and prints their full stat cards:
  - Total Runs, Innings, Batting Average, Strike Rate
  - Total Wickets, Bowling Average, Economy
"""

import pandas as pd
import random
import os
import sys
import io
from pathlib import Path

# Force UTF-8 output on Windows to avoid codec errors with Unicode symbols
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

ROOT         = Path(__file__).resolve().parents[2]
MATCHES_FILE = str(ROOT / 'matches.csv')

# ── Load ──────────────────────────────────────────────────────────────────────
def load(path):
    print(f"Loading {path}  (this may take a moment)...")
    df = pd.read_csv(path, dtype={
        'match_id':         str,
        'is_wicket':        int,
        'is_bowler_wicket': int,
        'runs_batter':      int,
        'runs_total':       int,
    })
    print(f"  Loaded {len(df):,} delivery rows.\n")
    return df

# ── Batting stats for one player ──────────────────────────────────────────────
def batting_stats(df, player):
    bat = df[df['batter'] == player]
    if bat.empty:
        return None

    # innings = distinct match_id + batting_team combinations where player batted
    innings_count = bat.groupby(['match_id', 'batting_team']).ngroups
    total_runs    = int(bat['runs_batter'].sum())
    dismissals    = int(bat['is_wicket'].sum())
    balls_faced   = len(bat)

    avg = round(total_runs / dismissals, 2) if dismissals > 0 else total_runs
    sr  = round((total_runs / balls_faced) * 100, 2) if balls_faced > 0 else 0

    return {
        'innings':       innings_count,
        'total_runs':    total_runs,
        'dismissals':    dismissals,
        'balls_faced':   balls_faced,
        'batting_avg':   avg,
        'strike_rate':   sr,
    }

# ── Bowling stats for one player ──────────────────────────────────────────────
def bowling_stats(df, player):
    bowl = df[df['bowler'] == player]
    if bowl.empty:
        return None

    wickets       = int(bowl['is_bowler_wicket'].sum())
    runs_conceded = int(bowl['runs_total'].sum())
    balls_bowled  = len(bowl)
    overs_bowled  = balls_bowled / 6

    bowl_avg = round(runs_conceded / wickets, 2) if wickets > 0 else 'N/A'
    economy  = round(runs_conceded / overs_bowled, 2) if overs_bowled > 0 else 0

    return {
        'wickets':       wickets,
        'runs_conceded': runs_conceded,
        'balls_bowled':  balls_bowled,
        'overs_bowled':  round(overs_bowled, 1),
        'bowl_avg':      bowl_avg,
        'economy':       economy,
    }

# ── Print a card ──────────────────────────────────────────────────────────────
def print_card(player, bat, bowl):
    sep = "─" * 55
    print(f"\n{'═'*55}")
    print(f"  🏏  {player}")
    print(f"{'═'*55}")

    if bat:
        print(f"  BATTING")
        print(f"  {sep}")
        print(f"  {'Innings':<22}: {bat['innings']}")
        print(f"  {'Total Runs':<22}: {bat['total_runs']}")
        print(f"  {'Dismissals':<22}: {bat['dismissals']}")
        print(f"  {'Balls Faced':<22}: {bat['balls_faced']}")
        print(f"  {'Batting Average':<22}: {bat['batting_avg']}")
        print(f"  {'Strike Rate':<22}: {bat['strike_rate']}")
    else:
        print(f"  BATTING : No batting records found.")

    print()

    if bowl:
        print(f"  BOWLING")
        print(f"  {sep}")
        print(f"  {'Total Wickets':<22}: {bowl['wickets']}")
        print(f"  {'Runs Conceded':<22}: {bowl['runs_conceded']}")
        print(f"  {'Overs Bowled':<22}: {bowl['overs_bowled']}")
        print(f"  {'Bowling Average':<22}: {bowl['bowl_avg']}")
        print(f"  {'Economy Rate':<22}: {bowl['economy']}")
    else:
        print(f"  BOWLING : No bowling records found.")

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    os.chdir(str(ROOT))

    df = load(MATCHES_FILE)

    # ── Choose 5 random players who appear BOTH as batter AND bowler (all-rounders
    #    or players with meaningful data on both sides)
    batters = set(df['batter'].unique())
    bowlers = set(df['bowler'].unique())
    all_players = list(batters | bowlers)

    # Seed for reproducibility — change seed to get different set
    random.seed(42)
    chosen = random.sample(all_players, 5)

    print(f"{'='*55}")
    print(f"  PLAYER STATS TEST — 5 Random Players")
    print(f"{'='*55}")
    print(f"  Selected: {', '.join(chosen)}\n")

    for player in chosen:
        bat  = batting_stats(df, player)
        bowl = bowling_stats(df, player)
        print_card(player, bat, bowl)

    print(f"\n{'='*55}")
    print("  Done. All 5 player stat cards printed.")
    print(f"{'='*55}\n")

if __name__ == '__main__':
    main()

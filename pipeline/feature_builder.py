import duckdb
import pandas as pd
from pathlib import Path
import os

# Config
DB_PATH = "data/processed/cricket.duckdb"
FEATURES_DIR = Path("data/features")

def build_global_features():
    """Computes advanced features for the entire dataset and stores them in Parquet."""
    if not Path(DB_PATH).exists():
        print(f"[-] Database not found at {DB_PATH}")
        return

    con = duckdb.connect(DB_PATH)
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)

    print("[*] Calculating Batting Features...")
    # Aggregating by match and batter
    batting_query = """
    SELECT 
        batter,
        date,
        match_id,
        competition,
        venue_name,
        bowling_team as opposition,
        SUM(runs_batter) as runs,
        SUM(is_wicket) as is_out,
        COUNT(*) as balls_faced
    FROM deliveries
    GROUP BY batter, date, match_id, competition, venue_name, opposition
    ORDER BY batter, date
    """
    batting_df = con.execute(batting_query).fetch_df()
    
    # Rolling features
    print("[*] Computing rolling averages...")
    batting_df['rolling_runs_5'] = batting_df.groupby('batter')['runs'].transform(lambda x: x.rolling(5, min_periods=1).mean().shift(1))
    
    # Rolling SR - handle potential division by zero
    def get_rolling_sr(group):
        runs_sum = group['runs'].rolling(5, min_periods=1).sum().shift(1)
        balls_sum = group['balls_faced'].rolling(5, min_periods=1).sum().shift(1)
        return (runs_sum / balls_sum * 100).fillna(0)

    batting_df['rolling_sr_5'] = batting_df.groupby('batter').apply(get_rolling_sr).reset_index(level=0, drop=True)

    # Opposition strength (proxy: average runs conceded by that team in that competition)
    print("[*] Computing competition/opposition strength...")
    opp_strength = batting_df.groupby(['opposition', 'competition'])['runs'].mean().reset_index(name='opp_avg_runs')
    batting_df = batting_df.merge(opp_strength, on=['opposition', 'competition'], how='left')

    # Save Batting Features
    batting_df.to_parquet(FEATURES_DIR / "batting_features.parquet")
    print(f"[+] Batting features saved to {FEATURES_DIR / 'batting_features.parquet'}")

    print("[*] Calculating Bowling Features...")
    bowling_query = """
    SELECT 
        bowler,
        date,
        match_id,
        competition,
        venue_name,
        batting_team as opposition,
        SUM(is_bowler_wicket) as wickets,
        SUM(runs_total) as runs_conceded,
        COUNT(*) as legal_balls
    FROM deliveries
    WHERE extras_wides = 0 AND extras_noballs = 0
    GROUP BY bowler, date, match_id, competition, venue_name, opposition
    ORDER BY bowler, date
    """
    bowling_df = con.execute(bowling_query).fetch_df()
    
    # Rolling features
    bowling_df['rolling_wickets_5'] = bowling_df.groupby('bowler')['wickets'].transform(lambda x: x.rolling(5, min_periods=1).mean().shift(1))
    
    def get_rolling_econ(group):
        runs_sum = group['runs_conceded'].rolling(5, min_periods=1).sum().shift(1)
        balls_sum = group['legal_balls'].rolling(5, min_periods=1).sum().shift(1)
        return (runs_sum / (balls_sum / 6)).fillna(0)
        
    bowling_df['rolling_econ_5'] = bowling_df.groupby('bowler').apply(get_rolling_econ).reset_index(level=0, drop=True)

    # Save Bowling Features
    bowling_df.to_parquet(FEATURES_DIR / "bowling_features.parquet")
    print(f"[+] Bowling features saved to {FEATURES_DIR / 'bowling_features.parquet'}")

    con.close()

if __name__ == "__main__":
    build_global_features()

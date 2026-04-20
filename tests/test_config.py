import os
import sys
import pandas as pd
import sqlite3
from pathlib import Path

# Path setup
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLE_SIZE = 5000
TEST_PLAYER = "Virat Kohli"
TEST_BOWLER = "JM Anderson"
TEST_COUNTRY = "India"
TEST_FORMAT = "ODI"

def get_sample_dataframe():
    """
    Returns a small sample of deliveries to keep tests lightweight.
    Uses LIMIT to satisfy low-end hardware constraints.
    """
    db_path = str(ROOT / "cricket.db")
    if not os.path.exists(db_path):
        # Fallback to creating a dummy df if DB is missing for CI/CD
        return pd.DataFrame(columns=[
            "batter", "bowler", "match_type", "city", "venue_name", 
            "innings", "over", "runs_batter", "runs_total", "is_wicket",
            "is_bowler_wicket", "match_id", "date", "bowler_type", "bowler_hand"
        ])

    try:
        conn = sqlite3.connect(db_path)
        # Query 5000 rows with a mix of data if possible, or just the first 5000
        # To make tests meaningful, we might want to include some rows for the TEST_PLAYER
        query = f"SELECT * FROM deliveries LIMIT {SAMPLE_SIZE}"
        df = pd.read_sql(query, conn)
        conn.close()
        return df
    except Exception as e:
        print(f"Error loading sample: {e}")
        return pd.DataFrame()

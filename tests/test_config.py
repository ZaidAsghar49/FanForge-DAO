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

# Define mock database path
MOCK_DB_PATH = ROOT / "tests" / "test_cricket_mock.db"

# Configure the environment variable so all imported scripts use the mock DB
os.environ["CRICKET_DB_PATH"] = str(MOCK_DB_PATH)

def build_mock_db():
    """Generates a small mock SQLite database for offline unit tests."""
    if MOCK_DB_PATH.exists():
        try:
            MOCK_DB_PATH.unlink()
        except Exception:
            pass

    conn = sqlite3.connect(str(MOCK_DB_PATH))
    cursor = conn.cursor()

    # 1. Create deliveries table
    cursor.execute("""
        CREATE TABLE deliveries (
            match_id TEXT, date TEXT, season TEXT, venue_name TEXT, city TEXT, country TEXT,
            match_type TEXT, competition TEXT, day_night TEXT, neutral_venue INTEGER DEFAULT 0,
            toss_winner TEXT, toss_decision TEXT, team_a TEXT, team_b TEXT, home_team TEXT,
            overs_limit INTEGER DEFAULT 0, innings INTEGER DEFAULT 0, over INTEGER DEFAULT 0,
            ball INTEGER DEFAULT 0, batting_team TEXT, bowling_team TEXT, match_phase TEXT,
            batter TEXT, non_striker TEXT, batting_position INTEGER DEFAULT 0, runs_batter INTEGER DEFAULT 0,
            is_wicket INTEGER DEFAULT 0, wicket_type TEXT, is_bowler_wicket INTEGER DEFAULT 0,
            bowler TEXT, bowler_type TEXT, bowler_hand TEXT, runs_total INTEGER DEFAULT 0,
            extras_wides INTEGER DEFAULT 0, extras_noballs INTEGER DEFAULT 0, extras_byes INTEGER DEFAULT 0,
            extras_legbyes INTEGER DEFAULT 0
        )
    """)

    # 2. Create players table
    cursor.execute("""
        CREATE TABLE players (
            match_id TEXT,
            team TEXT,
            player_name TEXT
        )
    """)

    # 3. Create matches table
    cursor.execute("""
        CREATE TABLE matches (
            match_id TEXT,
            match_format TEXT,
            season TEXT,
            date TEXT,
            venue TEXT,
            city TEXT,
            team1 TEXT,
            team2 TEXT,
            toss_winner TEXT,
            toss_decision TEXT,
            result TEXT,
            result_winner TEXT,
            result_margin INTEGER,
            result_unit TEXT,
            player_of_match TEXT,
            overs_per_inns INTEGER,
            total_deliveries INTEGER
        )
    """)

    # 4. Insert mock data matching test constraints
    deliveries_data = [
        # Melbourne, Australia, innings 1, over 0 (Powerplay), batter="V Kohli", bowler="JM Anderson" (Pace, Right)
        ("match_01", "2022-01-15", "2022", "Melbourne Cricket Ground", "Melbourne", "Australia", "ODI", "Asia Cup", "Day", 1, "India", "bat", "India", "Australia", "India", 50, 1, 0, 1, "India", "Australia", "Powerplay", "V Kohli", "Babar Azam", 3, 4, 0, None, 0, "JM Anderson", "Pace", "Right", 4, 0, 0, 0, 0),
        ("match_01", "2022-01-15", "2022", "Melbourne Cricket Ground", "Melbourne", "Australia", "ODI", "Asia Cup", "Day", 1, "India", "bat", "India", "Australia", "India", 50, 1, 0, 2, "India", "Australia", "Powerplay", "V Kohli", "Babar Azam", 3, 0, 0, None, 0, "JM Anderson", "Pace", "Right", 0, 0, 0, 0, 0),
        ("match_01", "2022-01-15", "2022", "Melbourne Cricket Ground", "Melbourne", "Australia", "ODI", "Asia Cup", "Day", 1, "India", "bat", "India", "Australia", "India", 50, 1, 0, 3, "India", "Australia", "Powerplay", "V Kohli", "Babar Azam", 3, 1, 0, None, 0, "JM Anderson", "Pace", "Right", 1, 0, 0, 0, 0),
        ("match_01", "2022-01-15", "2022", "Melbourne Cricket Ground", "Melbourne", "Australia", "ODI", "Asia Cup", "Day", 1, "India", "bat", "India", "Australia", "India", 50, 1, 0, 4, "India", "Australia", "Powerplay", "V Kohli", "Babar Azam", 3, 0, 1, "caught", 1, "JM Anderson", "Pace", "Right", 0, 0, 0, 0, 0),
        
        # Test phase, over 5, batter="V Kohli", bowler="A Zampa" (Spin, Right)
        ("match_02", "2023-06-20", "2023", "Lord's", "London", "England", "Test", "Ashes", "Day", 0, "England", "field", "India", "England", "India", 0, 1, 5, 1, "India", "England", "Middle", "V Kohli", "RG Sharma", 3, 4, 0, None, 0, "A Zampa", "Spin", "Right", 4, 0, 0, 0, 0),
        ("match_02", "2023-06-20", "2023", "Lord's", "London", "England", "Test", "Ashes", "Day", 0, "England", "field", "India", "England", "India", 0, 1, 5, 2, "India", "England", "Middle", "V Kohli", "RG Sharma", 3, 0, 1, "lbw", 1, "A Zampa", "Spin", "Right", 0, 0, 0, 0, 0),
        
        # Bowler style checks (Shaheen Afridi - Left-arm Pace)
        ("match_03", "2022-10-23", "2022", "MCG", "Melbourne", "Australia", "T20I", None, "Night", 0, "Pakistan", "field", "India", "Pakistan", "India", 20, 1, 2, 1, "India", "Pakistan", "Powerplay", "V Kohli", "KL Rahul", 3, 1, 0, None, 0, "Shaheen Afridi", "Pace", "Left", 1, 0, 0, 0, 0),
    ]

    # Generate 40 additional deliveries to provide non-zero stats
    for i in range(40):
        deliveries_data.append((
            "match_dyn", "2022-05-10", "2022", "Eden Gardens", "Kolkata", "India", "ODI", "Asia Cup", "Night", 0, "India", "bat", "India", "Pakistan", "India", 50, 1, 2, i % 6 + 1, "India", "Pakistan", "Powerplay", "V Kohli", "KL Rahul", 3, (i % 4), 0, None, 0, "Shaheen Afridi", "Pace", "Left", (i % 4), 0, 0, 0, 0
        ))

    cursor.executemany("INSERT INTO deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", deliveries_data)

    players_data = [
        ("match_01", "India", "Virat Kohli"),
        ("match_01", "India", "Rohit Sharma"),
        ("match_01", "Australia", "James Anderson"),
        ("match_02", "India", "Virat Kohli"),
        ("match_02", "England", "James Anderson"),
        ("match_03", "India", "Virat Kohli"),
        ("match_dyn", "India", "Virat Kohli"),
    ]
    cursor.executemany("INSERT INTO players VALUES (?,?,?)", players_data)

    matches_data = [
        ("match_01", "ODI", "2022", "2022-01-15", "Melbourne Cricket Ground", "Melbourne", "India", "Australia", "India", "bat", "normal", "India", 15, "runs", "V Kohli", 50, 600),
        ("match_02", "Test", "2023", "2023-06-20", "Lord's", "London", "England", "India", "England", "field", "normal", "India", 2, "wickets", "V Kohli", 0, 2000),
    ]
    cursor.executemany("INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", matches_data)

    conn.commit()
    conn.close()

# Auto-build database when this configuration module is imported
build_mock_db()

def get_sample_dataframe():
    """
    Returns a small sample of deliveries to keep tests lightweight.
    Uses the generated mock database.
    """
    try:
        conn = sqlite3.connect(str(MOCK_DB_PATH))
        query = f"SELECT * FROM deliveries LIMIT {SAMPLE_SIZE}"
        df = pd.read_sql(query, conn)
        conn.close()
        return df
    except Exception as e:
        print(f"Error loading sample: {e}")
        return pd.DataFrame()

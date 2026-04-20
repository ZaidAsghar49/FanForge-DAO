import pandas as pd
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PARQUET_PATH = ROOT / "matches.parquet"
DB_PATH = ROOT / "cricket.db"

def compare():
    print(f"Loading Parquet: {PARQUET_PATH}")
    df_p = pd.read_parquet(PARQUET_PATH)
    print(f"Parquet rows: {len(df_p)}")
    
    print(f"Querying SQLite row count: {DB_PATH}")
    con = sqlite3.connect(str(DB_PATH))
    count = con.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
    print(f"SQLite rows: {count}")
    con.close()

if __name__ == "__main__":
    compare()

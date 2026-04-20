import sqlite3
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "cricket.db"

def check_columns():
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        return
    
    con = sqlite3.connect(str(DB_PATH))
    df_head = pd.read_sql("SELECT * FROM deliveries LIMIT 1", con)
    print("Columns in 'deliveries' table:")
    print(df_head.columns.tolist())
    con.close()

if __name__ == "__main__":
    check_columns()

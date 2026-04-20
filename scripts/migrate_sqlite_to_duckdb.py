import duckdb
import sqlite3
import pandas as pd
from pathlib import Path
import time

# Config
SQLITE_DB = "cricket.db"
DUCKDB_PATH = Path("data/processed/cricket.duckdb")
MATCHES_CSV = "matches.csv"

def migrate_to_duckdb():
    """Migrates data from SQLite or CSV to DuckDB for high-performance analytics."""
    start_time = time.time()
    print(f"[*] Starting migration to DuckDB at {DUCKDB_PATH}...")
    
    # Connect to DuckDB
    con = duckdb.connect(str(DUCKDB_PATH))
    
    # If matches.csv exists, it's often faster to load directly from CSV
    if Path(MATCHES_CSV).exists():
        print(f"[*] Found {MATCHES_CSV}, loading directly into DuckDB...")
        con.execute(f"CREATE OR REPLACE TABLE deliveries AS SELECT * FROM read_csv_auto('{MATCHES_CSV}')")
    elif Path(SQLITE_DB).exists():
        print(f"[*] Loading from SQLite {SQLITE_DB}...")
        # DuckDB has a sqlite extension for direct migration
        con.execute("INSTALL sqlite; LOAD sqlite;")
        con.execute(f"CREATE OR REPLACE TABLE deliveries AS SELECT * FROM sqlite_scan('{SQLITE_DB}', 'deliveries')")
    else:
        print("[-] No source data found (matches.csv or cricket.db).")
        return

    # Add indexes for performance
    print("[*] Creating indexes...")
    con.execute("CREATE INDEX IF NOT EXISTS idx_batter ON deliveries (batter)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_bowler ON deliveries (bowler)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_match_id ON deliveries (match_id)")
    
    # Verify row count
    rows = con.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
    print(f"[+] Migration complete. Total rows in DuckDB: {rows:,}")
    print(f"[+] Time taken: {time.time() - start_time:.2f} seconds.")
    
    con.close()

if __name__ == "__main__":
    migrate_to_duckdb()

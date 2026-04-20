import duckdb
import sqlite3
import pandas as pd
from pathlib import Path
import time

# Config
SQLITE_DB = "cricket.db"
DUCKDB_PATH = Path("data/processed/cricket.duckdb")

def test_migration():
    """Migrates a tiny subset of data from SQLite to DuckDB to test performance."""
    start_time = time.time()
    print(f"[*] Starting test migration to DuckDB at {DUCKDB_PATH}...")
    
    # Ensure directory exists
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # Connect to DuckDB
    con = duckdb.connect(str(DUCKDB_PATH))
    
    try:
        print("[*] Loading first 10,000 rows from SQLite...")
        con.execute("INSTALL sqlite; LOAD sqlite;")
        # Note: LIMIT used on sqlite_scan might still scan some stuff, but it's a good test.
        con.execute(f"CREATE OR REPLACE TABLE deliveries_test AS SELECT * FROM sqlite_scan('{SQLITE_DB}', 'deliveries') LIMIT 10000")
        
        # Verify row count
        rows = con.execute("SELECT COUNT(*) FROM deliveries_test").fetchone()[0]
        print(f"[+] Test migration complete. Total rows in DuckDB (test table): {rows:,}")
        
    except Exception as e:
        print(f"[-] Error during test: {e}")
    finally:
        con.close()
        print(f"[+] Time taken: {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    test_migration()

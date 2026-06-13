import duckdb
import os
import time
from pathlib import Path

# Config
ROOT = Path(__file__).resolve().parents[1]
SQLITE_DB = str(ROOT / "cricket_india.db")
DUCKDB_PATH = str(ROOT / "data" / "processed" / "cricket_india.duckdb")

def migrate():
    """Performs a robust, high-performance migration from SQLite to DuckDB."""
    start_time = time.time()
    print("=== DuckDB Robust Migration Utility ===")
    print(f"[*] Source: {SQLITE_DB}")
    print(f"[*] Target: {DUCKDB_PATH}")
    
    if not os.path.exists(SQLITE_DB):
        print(f"[-] Error: Source SQLite database not found at {SQLITE_DB}")
        return

    # Ensure target directory exists
    os.makedirs(os.path.dirname(DUCKDB_PATH), exist_ok=True)

    print("[*] Connecting to DuckDB...")
    con = duckdb.connect(DUCKDB_PATH)
    
    try:
        print("[*] Loading SQLite extension...")
        con.execute("INSTALL sqlite; LOAD sqlite;")
        
        # Check if table already exists
        table_exists = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'deliveries'").fetchone()[0] > 0
        
        if table_exists:
            print("[*] Table 'deliveries' already exists. Checking row counts...")
            duck_rows = con.execute("SELECT count(*) FROM deliveries").fetchone()[0]
            print(f"[*] DuckDB current rows: {duck_rows:,}")
            # If it's already populated, maybe skip?
            # For robustness, we'll allow the user to decide or just recreate if it's a migration script.
            # Here we'll recreate to ensure fresh data for this transition.
            print("[*] Re-migrating to ensure data integrity...")
        
        print("[*] Transferring data (this may take a few minutes for 17GB)...")
        # Direct high-speed transfer
        con.execute(f"CREATE OR REPLACE TABLE deliveries AS SELECT * FROM sqlite_scan('{SQLITE_DB}', 'deliveries')")
        
        print("[*] Verifying transfer...")
        rows = con.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
        print(f"[+] Success! Total rows in DuckDB: {rows:,}")
        
        print("[*] Creating high-performance indexes...")
        con.execute("CREATE INDEX IF NOT EXISTS idx_batter ON deliveries (batter)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_bowler ON deliveries (bowler)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_match_id ON deliveries (match_id)")
        
        print("[*] Running checkpoint...")
        con.execute("CHECKPOINT")
        
    except Exception as e:
        print(f"[-] Critical Error during migration: {e}")
    finally:
        con.close()
        elapsed = time.time() - start_time
        print(f"=== Migration Finished in {elapsed:.2f} seconds ===")

if __name__ == "__main__":
    migrate()

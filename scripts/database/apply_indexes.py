import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "cricket.db"

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_deliveries_batter ON deliveries(batter);",
    "CREATE INDEX IF NOT EXISTS idx_deliveries_bowler ON deliveries(bowler);",
    "CREATE INDEX IF NOT EXISTS idx_deliveries_non_striker ON deliveries(non_striker);",
    "CREATE INDEX IF NOT EXISTS idx_batter_country_innings ON deliveries(batter, country, innings);",
    "CREATE INDEX IF NOT EXISTS idx_bowler_country_innings ON deliveries(bowler, country, innings);",
    "CREATE INDEX IF NOT EXISTS idx_batter_match_phase ON deliveries(batter, match_phase);",
    "CREATE INDEX IF NOT EXISTS idx_match_id_date ON deliveries(match_id, date);",
    "CREATE INDEX IF NOT EXISTS idx_venue_performance ON deliveries(venue_name, batter);"
]

def apply_indexes():
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        return
    
    print(f"Connecting to {DB_PATH}...")
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    
    for idx_sql in INDEXES:
        name = idx_sql.split(" ")[4]
        print(f"Applying index: {name}...")
        try:
            cur.execute(idx_sql)
            print(f"  ✔ Success.")
        except Exception as e:
            print(f"  ❌ Error: {e}")
            
    con.commit()
    con.close()
    print("All indexes applied.")

if __name__ == "__main__":
    apply_indexes()

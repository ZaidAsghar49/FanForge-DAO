import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "cricket.db"

def list_indexes():
    if not DB_PATH.exists():
        return
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='deliveries'")
    indexes = [r[0] for r in cur.fetchall()]
    print(f"Indexes on 'deliveries': {indexes}")
    con.close()

if __name__ == "__main__":
    list_indexes()

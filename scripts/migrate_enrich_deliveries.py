"""
Memory-frugal migration: processes batter_hand in match_id batches of 500.
Keeps DuckDB working set small at all times.
"""
import csv, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DUCKDB_PATH = str(ROOT / "data" / "processed" / "cricket.duckdb")
PLAYERS_CSV = str(ROOT / "Dataset" / "Players" / "players_data_with_all_info.csv")

import duckdb

def main():
    print("Opening DuckDB...")
    con = duckdb.connect(DUCKDB_PATH, read_only=False)
    try:
        con.execute("SET memory_limit='512MB'")
    except Exception:
        pass

    cols = [r[1] for r in con.execute("PRAGMA table_info(deliveries)").fetchall()]
    print(f"Columns: {len(cols)} | batter_hand: {'OK' if 'batter_hand' in cols else 'MISSING'} | ball_type: {'OK' if 'ball_type' in cols else 'MISSING'}")

    # -- Build hand lookup in Python (tiny dict) --
    hand_map = {}
    if os.path.exists(PLAYERS_CSV):
        with open(PLAYERS_CSV, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                n = (row.get("fullname") or "").strip()
                s = (row.get("battingstyle") or "").lower()
                if not n or n == "nan": continue
                h = "left" if "left" in s else ("right" if "right" in s else None)
                if h:
                    hand_map[n] = h
                    # abbreviated form
                    parts = n.split()
                    if len(parts) >= 2:
                        hand_map.setdefault(f"{parts[0][0]} {parts[-1]}", h)
    print(f"Hand map: {len(hand_map)} entries")

    # -- Batched batter_hand update: per unique batter name --
    print("\nFetching distinct batters needing batter_hand...")
    rows = con.execute("SELECT DISTINCT batter FROM deliveries WHERE batter_hand IS NULL AND batter IS NOT NULL").fetchall()
    batters_needing = [r[0] for r in rows]
    print(f"  {len(batters_needing):,} batters without hand assignment.")

    # Build just the (batter, hand) pairs we can resolve
    resolved = [(b, hand_map[b]) for b in batters_needing if b in hand_map]
    print(f"  Resolved {len(resolved):,} batters.")

    # Update ONE batter at a time — minimal working set
    CHUNK = 200
    for i in range(0, len(resolved), CHUNK):
        batch = resolved[i:i+CHUNK]
        for batter, hand in batch:
            # Use parameterized query to update just rows for that batter
            con.execute("UPDATE deliveries SET batter_hand = ? WHERE batter = ? AND batter_hand IS NULL",
                        [hand, batter])
        if i % 2000 == 0:
            con.execute("CHECKPOINT")
            done = i + len(batch)
            pct = done / len(resolved) * 100
            print(f"  Progress: {done:,}/{len(resolved):,} ({pct:.0f}%)")

    con.execute("CHECKPOINT")
    covered = con.execute("SELECT COUNT(*) FROM deliveries WHERE batter_hand IS NOT NULL").fetchone()[0]
    total   = con.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
    print(f"  Final coverage: {covered:,}/{total:,} ({covered/total*100:.1f}%)")

    # -- Derive ball_type (small query, only Test rows) --
    print("\nDeriving ball_type...")
    test_count = con.execute("SELECT COUNT(*) FROM deliveries WHERE match_type='Test'").fetchone()[0]
    print(f"  Test rows: {test_count:,}")
    
    # Set all to red first (just nulls)  
    con.execute("UPDATE deliveries SET ball_type = 'red' WHERE ball_type IS NULL OR ball_type = ''")
    # Pink = Test + day/night indicator
    con.execute("""UPDATE deliveries SET ball_type = 'pink'
                   WHERE match_type='Test'
                   AND (LOWER(COALESCE(day_night,'')) LIKE '%night%' OR LOWER(COALESCE(day_night,'')) LIKE '%d/n%')""")
    con.execute("CHECKPOINT")
    pink = con.execute("SELECT COUNT(*) FROM deliveries WHERE ball_type='pink'").fetchone()[0]
    print(f"  Pink ball rows: {pink:,}")

    # -- Fix NULL day_night --
    print("\nFixing NULL day_night for Test rows...")
    con.execute("""UPDATE deliveries SET day_night='Day'
                   WHERE match_type='Test' AND (day_night IS NULL OR TRIM(day_night) IN ('','Unknown'))""")
    con.execute("CHECKPOINT")

    # -- players_dim table (small) --
    print("\nCreating players_dim...")
    con.execute("DROP TABLE IF EXISTS players_dim")
    con.execute("CREATE TABLE players_dim (player_name VARCHAR, batter_hand VARCHAR)")
    batch_all = list(hand_map.items())
    for i in range(0, len(batch_all), 500):
        con.executemany("INSERT INTO players_dim VALUES (?,?)", batch_all[i:i+500])
    con.execute("CHECKPOINT")
    pdcnt = con.execute("SELECT COUNT(*) FROM players_dim").fetchone()[0]
    print(f"  players_dim: {pdcnt:,} rows")

    # -- Views --
    con.execute("DROP VIEW IF EXISTS v_ipl_deliveries")
    con.execute("CREATE VIEW v_ipl_deliveries AS SELECT * FROM deliveries WHERE competition='Indian Premier League'")
    con.execute("DROP VIEW IF EXISTS v_international")
    con.execute("CREATE VIEW v_international AS SELECT * FROM deliveries WHERE match_type IN ('Test','ODI','IT20','ODM','MDM')")
    print("Views created.")

    # Summary
    cols = [r[1] for r in con.execute("PRAGMA table_info(deliveries)").fetchall()]
    print(f"\nFinal columns: {len(cols)}")
    for req in ["batter_hand","ball_type","bowler_type","bowler_hand","match_phase","competition"]:
        print(f"  {req}: {'OK' if req in cols else 'MISSING'}")

    con.close()
    print("\nDone!")

if __name__ == "__main__":
    main()

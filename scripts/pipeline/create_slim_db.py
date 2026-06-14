"""
create_slim_db.py
-----------------
Creates a lean ~800-900 MB test DB from the full cricket.db.

Strategy: Pakistan + India batting teams, filtered to international formats only
          (Test, ODI, T20I) — excludes domestic/county cricket.

Usage:  python scripts/pipeline/create_slim_db.py

Output: cricket_slim.db  (target: 800-900 MB)
"""

import sqlite3
import os
import sys
import time

# ----------------------------------------------------------------
# CONFIGURATION — adjust to tune final size
# ----------------------------------------------------------------

FULL_DB_PATH = r"d:\University\Semester 8th\FYP\AI\cricket.db"
SLIM_DB_PATH = r"d:\University\Semester 8th\FYP\AI\cricket_slim.db"

# Teams to include (batting_team column)
# Pakistan alone ~572 MB | Pakistan+India ~1.29 GB
# Use 2-team combo with match_type filter to hit 800-900 MB
TARGET_TEAMS = ["Pakistan", "India"]

# Match types to include (match_type column) — excludes all domestic leagues
# International formats only: Test, ODI, T20I
# Set to None to include ALL (domestic too)
TARGET_MATCH_TYPES = ["Test", "ODI", "T20I"]

# ----------------------------------------------------------------

def fmt_size(path):
    if not os.path.exists(path):
        return "N/A"
    b = os.path.getsize(path)
    return f"{b/1e9:.2f} GB" if b > 1e9 else f"{b/1e6:.0f} MB"

def main():
    print("=" * 65)
    print("  CricketTruth -- Slim DB Creator")
    print("=" * 65)
    print(f"  Source : {FULL_DB_PATH}  ({fmt_size(FULL_DB_PATH)})")
    print(f"  Output : {SLIM_DB_PATH}")
    print(f"  Teams  : {TARGET_TEAMS}")
    print(f"  Formats: {TARGET_MATCH_TYPES or 'ALL'}")
    print("=" * 65)

    if not os.path.exists(FULL_DB_PATH):
        print(f"\n[ERROR] Source DB not found!")
        sys.exit(1)

    if os.path.exists(SLIM_DB_PATH):
        print(f"\n[INFO] Removing old slim DB...")
        os.remove(SLIM_DB_PATH)

    src = sqlite3.connect(FULL_DB_PATH)
    sc = src.cursor()

    # --- Build WHERE clause ---
    conditions = []
    params = []

    if TARGET_TEAMS:
        ph = ",".join("?" * len(TARGET_TEAMS))
        conditions.append(f"batting_team IN ({ph})")
        params.extend(TARGET_TEAMS)

    if TARGET_MATCH_TYPES:
        ph = ",".join("?" * len(TARGET_MATCH_TYPES))
        conditions.append(f"match_type IN ({ph})")
        params.extend(TARGET_MATCH_TYPES)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT * FROM deliveries {where}"

    # --- Count estimate first ---
    sc.execute(f"SELECT COUNT(*) FROM deliveries {where}", params)
    matching = sc.fetchone()[0]

    sc.execute("SELECT COUNT(*) FROM deliveries")
    total = sc.fetchone()[0]

    sc.execute("PRAGMA table_info(deliveries)")
    cols = [row[1] for row in sc.fetchall()]
    num_cols = len(cols)

    db_size_bytes = os.path.getsize(FULL_DB_PATH)
    bpr = db_size_bytes / total
    est_size_mb = (matching * bpr) / 1e6

    print(f"\n  Rows matched : {matching:,} / {total:,} ({matching/total*100:.1f}%)")
    print(f"  Est. DB size : {est_size_mb:.0f} MB")
    print()

    if est_size_mb < 500:
        print("  [WARN] Estimated size is below 500 MB. Consider adding more teams.")
    elif est_size_mb > 1200:
        print("  [WARN] Estimated size > 1.2 GB. Remove one team or restrict match_type.")
    else:
        print("  [OK] Estimated size is within acceptable range.")

    input("\n  Press ENTER to start extraction (Ctrl+C to cancel)...")

    # --- Create slim DB ---
    t0 = time.time()
    dst = sqlite3.connect(SLIM_DB_PATH)
    dc = dst.cursor()

    sc.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='deliveries'")
    create_sql = sc.fetchone()[0]
    dc.execute(create_sql)
    dst.commit()

    sc.execute(query, params)
    CHUNK = 50_000
    inserted = 0

    print(f"\n  Extracting rows...")
    while True:
        rows = sc.fetchmany(CHUNK)
        if not rows:
            break
        dc.executemany(
            f"INSERT INTO deliveries VALUES ({','.join('?'*num_cols)})",
            rows
        )
        dst.commit()
        inserted += len(rows)
        elapsed = time.time() - t0
        pct = inserted / matching * 100 if matching else 0
        eta = (elapsed / inserted * (matching - inserted)) if inserted > 0 else 0
        print(f"  {inserted:>10,} / {matching:,}  ({pct:5.1f}%)  "
              f"ETA: {eta:.0f}s     ", end="\r", flush=True)

    # --- Copy players table ---
    sc.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='players'")
    player_schema = sc.fetchone()
    if player_schema:
        dc.execute(player_schema[0])
        sc.execute("SELECT * FROM players")
        p_rows = sc.fetchall()
        if p_rows:
            sc.execute("PRAGMA table_info(players)")
            p_cols = len(sc.fetchall())
            dc.executemany(f"INSERT INTO players VALUES ({','.join('?'*p_cols)})", p_rows)
        dst.commit()
        print(f"\n  Players table copied: {len(p_rows) if p_rows else 0} rows")

    # --- Indexes ---
    print("  Creating indexes...")
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_batting_team ON deliveries(batting_team)",
        "CREATE INDEX IF NOT EXISTS idx_match_id ON deliveries(match_id)",
        "CREATE INDEX IF NOT EXISTS idx_match_type ON deliveries(match_type)",
        "CREATE INDEX IF NOT EXISTS idx_bowler ON deliveries(bowler_id)",
        "CREATE INDEX IF NOT EXISTS idx_striker ON deliveries(striker_id)",
        "CREATE INDEX IF NOT EXISTS idx_date ON deliveries(date)",
    ]:
        dc.execute(idx_sql)
    dst.commit()

    src.close()
    dst.close()

    elapsed_total = time.time() - t0
    final_size = fmt_size(SLIM_DB_PATH)
    final_bytes = os.path.getsize(SLIM_DB_PATH)

    print(f"\n{'='*65}")
    print(f"  DONE in {elapsed_total:.0f}s")
    print(f"  Rows inserted : {inserted:,}")
    print(f"  Final DB size : {final_size}")
    print(f"  Output path   : {SLIM_DB_PATH}")

    if 800e6 <= final_bytes <= 950e6:
        print("\n  TARGET HIT: DB is in the 800-900 MB range!")
    elif final_bytes < 800e6:
        print(f"\n  Under target by {(800e6 - final_bytes)/1e6:.0f} MB. Add more teams.")
    else:
        print(f"\n  Over target by {(final_bytes - 900e6)/1e6:.0f} MB. Reduce teams.")
    print("=" * 65)

if __name__ == "__main__":
    main()

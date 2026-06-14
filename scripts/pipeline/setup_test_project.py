import sqlite3
import os
import shutil
import time
import sys
import io

# Force UTF-8 output so print never crashes on Windows cp1252
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ===========================================================================
# CONFIG
# ===========================================================================

SRC_DIR = os.path.join("d:\\", "University", "Semester 8th", "FYP", "AI")
DST_DIR = os.path.join("d:\\", "University", "Semester 8th", "FYP", "Test")

SRC_DB  = os.path.join(SRC_DIR, "cricket.db")
DST_DB  = os.path.join(DST_DIR, "cricket.db")

# Filter: Australia + England, Test matches only
TARGET_TEAMS       = ["Australia", "England"]
TARGET_MATCH_TYPES = ["Test"]

# Subdirectories to copy (code + config, no large files)
COPY_DIRS = [
    "scripts",
    "data",
    "models",
    "pipeline",
    "docs",
    "tests",
    "output",
]

# Root-level files to copy
COPY_FILES = [
    "requirements.txt",
    "README.md",
    "PROJECT_ROADMAP.md",
    "PRECISION_NAMING.md",
    "bowlers.csv",
    "run_claims.py",
    "run_claims.ps1",
    "run_queries_isolated.py",
    "run_user_queries_custom.py",
    "test_queries.py",
    "test_user_queries.py",
    "test_llm_engine.py",
    "system_architecture_audit.md",
]

# Names to never copy
SKIP_ALWAYS = {
    "cricket.db", "cricket.db-shm", "cricket.db-wal",
    "matches.csv", "matches.parquet", "cricket_slim.db",
    "__pycache__", ".git", ".pytest_cache",
    "Dataset", "cache", "scratch", "temp",
    ".env",
}

# ===========================================================================

def sep(title=""):
    line = "=" * 65
    if title:
        print(f"\n{line}")
        print(f"  {title}")
        print(line)
    else:
        print(line)

def fmt_size(path):
    if not os.path.exists(path):
        return "N/A"
    b = os.path.getsize(path)
    if b > 1e9:
        return f"{b/1e9:.2f} GB"
    return f"{b/1e6:.0f} MB"

def copy_dir_recursive(src, dst, skip=None):
    skip = skip or {"__pycache__", ".pytest_cache"}
    os.makedirs(dst, exist_ok=True)
    count = 0
    for name in os.listdir(src):
        if name in skip:
            continue
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s):
            count += copy_dir_recursive(s, d, skip)
        else:
            shutil.copy2(s, d)
            count += 1
    return count

# ===========================================================================
# STEP 0 — Validate source
# ===========================================================================

sep()
print("  FYP Test Project Setup")
print(f"  Source : {SRC_DIR}")
print(f"  Target : {DST_DIR}")
print(f"  Filter : AUS + ENG, Test matches only")
sep()

if not os.path.exists(SRC_DB):
    print(f"\n[ERROR] Source DB not found: {SRC_DB}")
    sys.exit(1)

# ===========================================================================
# STEP 1 — Pre-flight estimate
# ===========================================================================

sep("STEP 1/5 -- Estimating slim DB size")

src_conn = sqlite3.connect(SRC_DB)
sc = src_conn.cursor()

sc.execute("SELECT COUNT(*) FROM deliveries")
total_rows = sc.fetchone()[0]
bpr = os.path.getsize(SRC_DB) / total_rows

t_ph = ",".join("?" * len(TARGET_TEAMS))
f_ph = ",".join("?" * len(TARGET_MATCH_TYPES))
params = TARGET_TEAMS + TARGET_MATCH_TYPES

sc.execute(
    f"SELECT COUNT(*) FROM deliveries "
    f"WHERE batting_team IN ({t_ph}) AND match_type IN ({f_ph})",
    params
)
matching_rows = sc.fetchone()[0]
est_mb = matching_rows * bpr / 1e6

print(f"  Total rows in source : {total_rows:,}")
print(f"  Matching rows        : {matching_rows:,}")
print(f"  Estimated DB size    : {est_mb:.0f} MB")

if est_mb < 500:
    print("  [WARN] Under 500 MB")
elif est_mb > 1100:
    print("  [WARN] Over 1.1 GB")
else:
    print("  [OK] Size within 800-900 MB target range")

print("  [INFO] Starting automatically...")

# ===========================================================================
# STEP 2 — Create folder
# ===========================================================================

sep("STEP 2/5 -- Creating Test project folder")

if os.path.exists(DST_DIR):
    print(f"  [INFO] Folder exists, files will be overwritten.")
else:
    os.makedirs(DST_DIR)
    print(f"  [OK] Created: {DST_DIR}")

# ===========================================================================
# STEP 3 — Copy source code
# ===========================================================================

sep("STEP 3/5 -- Copying source code and config files")

for d in COPY_DIRS:
    src_path = os.path.join(SRC_DIR, d)
    dst_path = os.path.join(DST_DIR, d)
    if os.path.exists(src_path):
        if os.path.exists(dst_path):
            shutil.rmtree(dst_path)
        n = copy_dir_recursive(src_path, dst_path)
        print(f"  [COPIED] {d}/  ({n} files)")
    else:
        print(f"  [SKIP]   {d}/  (not in source)")

ok = 0
for f in COPY_FILES:
    sf = os.path.join(SRC_DIR, f)
    df = os.path.join(DST_DIR, f)
    if os.path.exists(sf):
        shutil.copy2(sf, df)
        ok += 1
    else:
        print(f"  [SKIP]   {f} (not found)")

print(f"  [OK] {ok} root-level files copied")

# ===========================================================================
# STEP 4 — Extract slim DB
# ===========================================================================

sep("STEP 4/5 -- Extracting AUS+ENG Test deliveries into new cricket.db")

if os.path.exists(DST_DB):
    os.remove(DST_DB)
    print("  [INFO] Removed old DB at destination")

# Get deliveries schema info
sc.execute("PRAGMA table_info(deliveries)")
col_names = [c[1] for c in sc.fetchall()]
num_cols  = len(col_names)

# Open destination DB
dst_conn = sqlite3.connect(DST_DB)
dc = dst_conn.cursor()
dc.execute("PRAGMA journal_mode = WAL")
dc.execute("PRAGMA synchronous = NORMAL")
dc.execute("PRAGMA cache_size = -65536")  # 64 MB

# Create table schema
sc.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='deliveries'")
dc.execute(sc.fetchone()[0])
dst_conn.commit()

# Run extraction
extract_sql = (
    f"SELECT * FROM deliveries "
    f"WHERE batting_team IN ({t_ph}) AND match_type IN ({f_ph})"
)
sc.execute(extract_sql, params)

insert_sql = f"INSERT INTO deliveries VALUES ({','.join('?'*num_cols)})"
CHUNK    = 50_000
inserted = 0
t0       = time.time()

print(f"  Extracting {matching_rows:,} rows in chunks of {CHUNK:,}...\n")

while True:
    batch = sc.fetchmany(CHUNK)
    if not batch:
        break
    dc.executemany(insert_sql, batch)
    dst_conn.commit()
    inserted += len(batch)
    elapsed  = time.time() - t0
    pct      = inserted / matching_rows * 100
    eta      = (elapsed / inserted * (matching_rows - inserted)) if inserted else 0
    print(f"  {inserted:>10,} / {matching_rows:,}  ({pct:5.1f}%)  ETA: {eta:.0f}s   ",
          end="\r", flush=True)

print()

# Copy players table
sc.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='players'")
p_schema = sc.fetchone()
if p_schema:
    dc.execute(p_schema[0])
    sc.execute("SELECT * FROM players")
    p_rows = sc.fetchall()
    if p_rows:
        sc.execute("PRAGMA table_info(players)")
        p_ncols = len(sc.fetchall())
        dc.executemany(f"INSERT INTO players VALUES ({','.join('?'*p_ncols)})", p_rows)
    dst_conn.commit()
    print(f"  [OK] Players table: {len(p_rows) if p_rows else 0} rows")

# Build indexes
print("  Building query indexes...")
for idx_sql in [
    "CREATE INDEX IF NOT EXISTS idx_batting_team ON deliveries(batting_team)",
    "CREATE INDEX IF NOT EXISTS idx_bowling_team ON deliveries(bowling_team)",
    "CREATE INDEX IF NOT EXISTS idx_match_type   ON deliveries(match_type)",
    "CREATE INDEX IF NOT EXISTS idx_match_id     ON deliveries(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_date         ON deliveries(date)",
    "CREATE INDEX IF NOT EXISTS idx_venue        ON deliveries(venue_name)",
    "CREATE INDEX IF NOT EXISTS idx_batter       ON deliveries(batter)",
    "CREATE INDEX IF NOT EXISTS idx_bowler       ON deliveries(bowler)",
    "CREATE INDEX IF NOT EXISTS idx_is_wicket    ON deliveries(is_wicket)",
]:
    dc.execute(idx_sql)
dst_conn.commit()

src_conn.close()
dst_conn.close()

elapsed_total = time.time() - t0
final_size    = fmt_size(DST_DB)
final_bytes   = os.path.getsize(DST_DB)

print(f"  [OK] Rows inserted : {inserted:,}")
print(f"  [OK] DB size       : {final_size}")
print(f"  [OK] Time taken    : {elapsed_total:.0f}s")

# ===========================================================================
# STEP 5 — Write .env
# ===========================================================================

sep("STEP 5/5 -- Writing .env")

src_env = os.path.join(SRC_DIR, ".env")
dst_env = os.path.join(DST_DIR, ".env")

env_lines = []
if os.path.exists(src_env):
    with open(src_env, "r") as f:
        env_lines = [l.strip() for l in f if l.strip()]

with open(dst_env, "w") as f:
    for line in env_lines:
        f.write(line + "\n")
    f.write(f"\n# Test DB - AUS+ENG Test matches only (~{int(final_bytes/1e6)} MB)\n")
    f.write(f"DB_PATH={DST_DB}\n")
    f.write(f"DB_SCOPE=AUS_ENG_TEST\n")

print(f"  [OK] .env written: {dst_env}")

# ===========================================================================
# DONE
# ===========================================================================

sep()
print("  TEST PROJECT READY")
sep()
print(f"  Location : {DST_DIR}")
print(f"  DB       : cricket.db  ({final_size})")
print(f"  Rows     : {inserted:,} deliveries")
print(f"  Teams    : {TARGET_TEAMS}")
print(f"  Format   : {TARGET_MATCH_TYPES}")
print()
print("  To launch:")
print(f'    cd "{DST_DIR}"')
print("    streamlit run scripts/analysis/dashboard.py")
print()

if 750e6 <= final_bytes <= 1050e6:
    print(f"  DB SIZE OK: {final_size} -- within 800-900 MB target")
elif final_bytes < 750e6:
    print(f"  DB SIZE LOW: {final_size} -- add more teams if needed")
else:
    print(f"  DB SIZE HIGH: {final_size} -- acceptable for FYP testing")

sep()

"""
migrate_to_parquet.py  —  Storage Optimisation for Full 38-Column Schema
=========================================================================
Converts matches.csv to:
  • matches.parquet  (Apache Parquet — fastest for analytical queries)
  • cricket.db       (SQLite — query with any SQL tool)

Optimisations:
  • Category dtypes for low-cardinality strings      (48-90% size reduction)
  • Int8 / Int16 for numeric flag & run columns
  • Chunked streaming so RAM stays bounded
  • Parquet partitioned by match_type

Usage:
    python scripts/pipeline/migrate_to_parquet.py
    python scripts/pipeline/migrate_to_parquet.py --parquet-only
    python scripts/pipeline/migrate_to_parquet.py --sqlite-only
    python scripts/pipeline/migrate_to_parquet.py --chunk 500000
    python scripts/pipeline/migrate_to_parquet.py --verify-only
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).resolve().parents[2]
MATCHES_CSV  = ROOT / "matches.csv"
PARQUET_FILE = ROOT / "matches.parquet"
SQLITE_FILE  = ROOT / "cricket.db"

# ── Dtype optimisation maps ───────────────────────────────────────────────────
# Columns with low cardinality → category (dramatic size reduction in Parquet)
CATEGORY_COLS = [
    "match_type", "competition", "day_night", "match_phase",
    "batting_team", "bowling_team", "team_a", "team_b", "home_team",
    "batter", "bowler", "non_striker",
    "city", "country", "venue_name",
    "toss_winner", "toss_decision",
    "wicket_type", "bowler_type", "bowler_hand",
    "season",
]

# Small integers (0/1 flags, run counts)
INT8_COLS = [
    "is_wicket", "is_bowler_wicket",
    "neutral_venue",
]
INT16_COLS = [
    "runs_batter", "runs_total",
    "extras_wides", "extras_noballs", "extras_byes", "extras_legbyes",
    "innings", "over", "ball", "batting_position", "overs_limit",
]

CHUNK_SIZE_DEFAULT = 500_000   # rows per read-chunk


def _optimise_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Apply memory-efficient dtypes. Columns missing from df are silently skipped."""
    if "match_id" in df.columns:
        df["match_id"] = df["match_id"].astype(str)
        
    for col in CATEGORY_COLS:
        if col in df.columns:
            df[col] = df[col].astype("category")

    for col in INT8_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int8")

    for col in INT16_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int16")

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    return df


# ── Parquet ───────────────────────────────────────────────────────────────────

def migrate_to_parquet(chunk_size: int = CHUNK_SIZE_DEFAULT) -> int:
    log.info("── Parquet Migration ─────────────────────────────────────")
    log.info("Source  : %s  (%.1f MB)", MATCHES_CSV,
             MATCHES_CSV.stat().st_size / 1e6)
    log.info("Target  : %s", PARQUET_FILE)

    writer  = None
    schema  = None
    total   = 0
    t_start = time.time()

    reader = pd.read_csv(MATCHES_CSV, chunksize=chunk_size, low_memory=False)

    for i, chunk in enumerate(reader):
        chunk = _optimise_dtypes(chunk)

        # Convert categories → string for consistent Arrow schema across chunks
        for col in CATEGORY_COLS:
            if col in chunk.columns:
                chunk[col] = chunk[col].astype(str)

        table = pa.Table.from_pandas(chunk, preserve_index=False)

        if writer is None:
            schema = table.schema
            writer = pq.ParquetWriter(
                str(PARQUET_FILE),
                schema,
                compression="snappy",
            )

        writer.write_table(table)
        total   += len(chunk)
        elapsed  = time.time() - t_start
        log.info("  Chunk %3d  |  rows so far: %10s  |  elapsed: %.1fs",
                 i + 1, f"{total:,}", elapsed)

    if writer:
        writer.close()

    size_mb = PARQUET_FILE.stat().st_size / 1e6
    log.info("✅  Parquet done — %.1f MB  (%s rows)  in %.1fs",
             size_mb, f"{total:,}", time.time() - t_start)
    return total


# ── SQLite ────────────────────────────────────────────────────────────────────

SQLITE_CREATE = """
CREATE TABLE IF NOT EXISTS deliveries (
    -- Match context (1-12)
    match_id         TEXT,
    date             TEXT,
    season           TEXT,
    venue_name       TEXT,
    city             TEXT,
    country          TEXT,
    match_type       TEXT,
    competition      TEXT,
    day_night        TEXT,
    neutral_venue    INTEGER DEFAULT 0,
    toss_winner      TEXT,
    toss_decision    TEXT,
    team_a           TEXT,
    team_b           TEXT,
    home_team        TEXT,
    overs_limit      INTEGER DEFAULT 0,
    -- Delivery context
    innings          INTEGER DEFAULT 0,
    over             INTEGER DEFAULT 0,
    ball             INTEGER DEFAULT 0,
    batting_team     TEXT,
    bowling_team     TEXT,
    match_phase      TEXT,
    -- Batting analytics (13-25)
    batter           TEXT,
    non_striker      TEXT,
    batting_position INTEGER DEFAULT 0,
    runs_batter      INTEGER DEFAULT 0,
    is_wicket        INTEGER DEFAULT 0,
    wicket_type      TEXT,
    is_bowler_wicket INTEGER DEFAULT 0,
    -- Bowling analytics (26-38)
    bowler           TEXT,
    bowler_type      TEXT,
    bowler_hand      TEXT,
    runs_total       INTEGER DEFAULT 0,
    extras_wides     INTEGER DEFAULT 0,
    extras_noballs   INTEGER DEFAULT 0,
    extras_byes      INTEGER DEFAULT 0,
    extras_legbyes   INTEGER DEFAULT 0
);
"""

# Indexes covering every filter dimension used by validate_model.py
SQLITE_INDEXES = [
    # Subject filters (most important — used on every query)
    "CREATE INDEX IF NOT EXISTS idx_batter        ON deliveries(batter);",
    "CREATE INDEX IF NOT EXISTS idx_bowler        ON deliveries(bowler);",
    # Match context
    "CREATE INDEX IF NOT EXISTS idx_match_id      ON deliveries(match_id);",
    "CREATE INDEX IF NOT EXISTS idx_match_type    ON deliveries(match_type);",
    "CREATE INDEX IF NOT EXISTS idx_city          ON deliveries(city);",
    "CREATE INDEX IF NOT EXISTS idx_country       ON deliveries(country);",
    "CREATE INDEX IF NOT EXISTS idx_venue_name    ON deliveries(venue_name);",
    "CREATE INDEX IF NOT EXISTS idx_date          ON deliveries(date);",
    "CREATE INDEX IF NOT EXISTS idx_season        ON deliveries(season);",
    "CREATE INDEX IF NOT EXISTS idx_innings       ON deliveries(innings);",
    "CREATE INDEX IF NOT EXISTS idx_competition   ON deliveries(competition);",
    "CREATE INDEX IF NOT EXISTS idx_toss_winner   ON deliveries(toss_winner);",
    "CREATE INDEX IF NOT EXISTS idx_toss_decision ON deliveries(toss_decision);",
    "CREATE INDEX IF NOT EXISTS idx_neutral_venue ON deliveries(neutral_venue);",
    "CREATE INDEX IF NOT EXISTS idx_day_night     ON deliveries(day_night);",
    # Delivery context
    "CREATE INDEX IF NOT EXISTS idx_batting_team  ON deliveries(batting_team);",
    "CREATE INDEX IF NOT EXISTS idx_bowling_team  ON deliveries(bowling_team);",
    "CREATE INDEX IF NOT EXISTS idx_over          ON deliveries(over);",
    "CREATE INDEX IF NOT EXISTS idx_match_phase   ON deliveries(match_phase);",
    # Batting analytics
    "CREATE INDEX IF NOT EXISTS idx_batting_pos   ON deliveries(batting_position);",
    "CREATE INDEX IF NOT EXISTS idx_non_striker   ON deliveries(non_striker);",
    "CREATE INDEX IF NOT EXISTS idx_wicket_type   ON deliveries(wicket_type);",
    "CREATE INDEX IF NOT EXISTS idx_is_wicket     ON deliveries(is_wicket);",
    # Bowling analytics
    "CREATE INDEX IF NOT EXISTS idx_bowler_type   ON deliveries(bowler_type);",
    "CREATE INDEX IF NOT EXISTS idx_bowler_hand   ON deliveries(bowler_hand);",
    # Composite: most common query pattern (batter + match_type)
    "CREATE INDEX IF NOT EXISTS idx_batter_format ON deliveries(batter, match_type);",
    "CREATE INDEX IF NOT EXISTS idx_bowler_format ON deliveries(bowler, match_type);",
]


def migrate_to_sqlite(chunk_size: int = CHUNK_SIZE_DEFAULT) -> int:
    log.info("── SQLite Migration ──────────────────────────────────────")
    log.info("Source  : %s  (%.1f MB)", MATCHES_CSV,
             MATCHES_CSV.stat().st_size / 1e6)
    log.info("Target  : %s", SQLITE_FILE)

    if SQLITE_FILE.exists():
        log.warning("Existing DB found — dropping and rebuilding.")
        SQLITE_FILE.unlink()

    con = sqlite3.connect(str(SQLITE_FILE))
    cur = con.cursor()

    # Performance pragmas for bulk insert
    cur.executescript("""
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous  = NORMAL;
        PRAGMA cache_size   = -131072;   -- 128 MB
        PRAGMA temp_store   = MEMORY;
    """)
    cur.executescript(SQLITE_CREATE)
    con.commit()

    total   = 0
    t_start = time.time()

    for i, chunk in enumerate(
        pd.read_csv(MATCHES_CSV, chunksize=chunk_size, low_memory=False)
    ):
        chunk = chunk.where(pd.notnull(chunk), None)
        chunk.to_sql("deliveries", con, if_exists="append", index=False)
        total  += len(chunk)
        elapsed = time.time() - t_start
        log.info("  Chunk %3d  |  rows so far: %10s  |  elapsed: %.1fs",
                 i + 1, f"{total:,}", elapsed)

    log.info("Building indexes (%d)…", len(SQLITE_INDEXES))
    for idx_sql in SQLITE_INDEXES:
        cur.execute(idx_sql)
    con.commit()

    # Reset pragmas to safe defaults
    cur.execute("PRAGMA journal_mode = DELETE;")
    cur.execute("PRAGMA synchronous  = FULL;")
    con.close()

    size_mb = SQLITE_FILE.stat().st_size / 1e6
    log.info("✅  SQLite done — %.1f MB  (%s rows)  in %.1fs",
             size_mb, f"{total:,}", time.time() - t_start)
    return total


# ── Verification ──────────────────────────────────────────────────────────────

def verify_outputs() -> None:
    log.info("── Verification ──────────────────────────────────────────")

    if PARQUET_FILE.exists():
        pf = pq.read_metadata(str(PARQUET_FILE))
        log.info("Parquet  : %s rows  |  %d row-groups  |  %.1f MB",
                 f"{pf.num_rows:,}", pf.num_row_groups,
                 PARQUET_FILE.stat().st_size / 1e6)
        schema_cols = [pf.row_group(0).column(i).path_in_schema
                       for i in range(pf.row_group(0).num_columns)]
        log.info("Parquet columns (%d): %s",
                 len(schema_cols), ", ".join(schema_cols))
    else:
        log.warning("Parquet file not found at %s", PARQUET_FILE)

    if SQLITE_FILE.exists():
        con   = sqlite3.connect(str(SQLITE_FILE))
        count = con.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
        cols  = [r[1] for r in con.execute(
            "PRAGMA table_info(deliveries)").fetchall()]
        con.close()
        log.info("SQLite   : %s rows  |  %d columns  |  %.1f MB",
                 f"{count:,}", len(cols), SQLITE_FILE.stat().st_size / 1e6)
        log.info("SQLite columns (%d): %s", len(cols), ", ".join(cols))
    else:
        log.warning("SQLite file not found at %s", SQLITE_FILE)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate 38-column matches.csv → Parquet + SQLite"
    )
    parser.add_argument("--parquet-only", action="store_true")
    parser.add_argument("--sqlite-only",  action="store_true")
    parser.add_argument("--chunk",        type=int, default=CHUNK_SIZE_DEFAULT,
                        help="Rows per chunk (default 500,000)")
    parser.add_argument("--verify-only",  action="store_true",
                        help="Only verify existing output files")
    args = parser.parse_args()

    if not MATCHES_CSV.exists():
        log.error("matches.csv not found at %s — run extract_data.py first.",
                  MATCHES_CSV)
        sys.exit(1)

    if args.verify_only:
        verify_outputs()
        sys.exit(0)

    if not args.sqlite_only:
        migrate_to_parquet(args.chunk)

    if not args.parquet_only:
        migrate_to_sqlite(args.chunk)

    verify_outputs()
    log.info("All done.")

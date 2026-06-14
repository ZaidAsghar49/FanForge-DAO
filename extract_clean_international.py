"""
extract_clean_international.py
==============================
Clean-room international cricket data pipeline.

Destination isolation:
  SQLite  →  Dataset/Processed/cricket_clean.db
  Parquet →  Dataset/Processed/matches_clean.parquet

Hard filters:
  • match_type  ∈ {"ODI", "Test"}  (discards T20, T10, IT20, MDM, ODM, etc.)
  • info.dates[0] ∈ [2019-01-01, 2026-06-30]
  • info.gender == "male"

RAM cap: ≤250 MB – files are read one-at-a-time; Parquet is written
incrementally via ParquetWriter(compression='ZSTD').

7 composite indexes are created at the end for sub-second web-query
performance on cricket_clean.db.

Existing files (cricket.db, cricket_india.db, cricket_test.db,
matches.parquet, matches.csv …) are NEVER touched.
"""

import json
import os
import sqlite3
import sys
import time
import argparse
import traceback
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Dependency guard – pyarrow is optional; we inform the user if absent
# ---------------------------------------------------------------------------
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False
    print(
        "[WARN] pyarrow is not installed – Parquet output will be skipped.\n"
        "       Install it with:  pip install pyarrow"
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ALLOWED_FORMATS: frozenset = frozenset({"ODI", "Test"})
BLOCKED_FORMATS: frozenset = frozenset({"T20", "T10", "IT20", "MDM", "ODM", "T20I"})
DATE_MIN = date(2019, 1, 1)
DATE_MAX = date(2026, 6, 30)
REQUIRED_GENDER = "male"

SOURCE_DIR   = Path("Dataset/Matches")
DB_OUT_PATH  = Path("Dataset/Processed/cricket_clean.db")
PQ_OUT_PATH  = Path("Dataset/Processed/matches_clean.parquet")

DELIVERY_BATCH_SIZE = 50_000   # rows flushed per SQLite executemany call
MATCH_ROW_BUFFER    = 5_000    # match-level rows buffered before parquet write

# ---------------------------------------------------------------------------
# Explicit PyArrow schema (prevents schema-inference drift across file batches)
# ---------------------------------------------------------------------------
PARQUET_SCHEMA = pa.schema([
    ("match_id",       pa.string()),
    ("match_format",   pa.string()),
    ("season",         pa.string()),
    ("date",           pa.string()),
    ("venue",          pa.string()),
    ("city",           pa.string()),
    ("gender",         pa.string()),
    ("team1",          pa.string()),
    ("team2",          pa.string()),
    ("toss_winner",    pa.string()),
    ("toss_decision",  pa.string()),
    ("result",         pa.string()),
    ("result_winner",  pa.string()),
    ("result_margin",  pa.int32()),
    ("result_unit",    pa.string()),
    ("player_of_match",pa.string()),
    ("overs_per_inns", pa.int32()),
    ("total_deliveries", pa.int32()),
])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_str(val) -> str:
    """Return val as str, or empty string for None / non-str."""
    return str(val) if val is not None else ""


def _parse_date(raw_date) -> date | None:
    """Parse ISO date string; return None on failure."""
    if not raw_date:
        return None
    try:
        return datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _extract_match_row(match_id: str, info: dict, total_deliveries: int) -> dict:
    """Build a flat dict representing the match-level parquet row."""
    teams   = info.get("teams", [])
    toss    = info.get("toss", {})
    outcome = info.get("outcome", {})

    # outcome can be: {winner:…, by:{runs:N}} or {result:"tie"} etc.
    result_winner = outcome.get("winner", None)
    result        = None
    result_margin = None
    result_unit   = None

    by = outcome.get("by", {})
    if by:
        for unit, val in by.items():          # e.g. {"runs": 45} or {"wickets": 3}
            result_unit   = unit
            result_margin = int(val) if val is not None else None
            break
    if "result" in outcome:
        result = outcome["result"]            # "tie", "no result", etc.

    pom_list = info.get("player_of_match", [])
    pom      = ", ".join(pom_list) if pom_list else None

    return {
        "match_id":          match_id,
        "match_format":      info.get("match_type", ""),
        "season":            _safe_str(info.get("season")),
        "date":              _safe_str(info.get("dates", [None])[0]),
        "venue":             _safe_str(info.get("venue")),
        "city":              _safe_str(info.get("city")),
        "gender":            _safe_str(info.get("gender")),
        "team1":             _safe_str(teams[0]) if len(teams) > 0 else "",
        "team2":             _safe_str(teams[1]) if len(teams) > 1 else "",
        "toss_winner":       _safe_str(toss.get("winner")),
        "toss_decision":     _safe_str(toss.get("decision")),
        "result":            result,
        "result_winner":     result_winner,
        "result_margin":     result_margin,
        "result_unit":       result_unit,
        "player_of_match":   pom,
        "overs_per_inns":    info.get("overs"),           # None for Tests
        "total_deliveries":  total_deliveries,
    }


def _schema_row_to_arrays(rows: list[dict]) -> pa.Table:
    """Convert a list of match-row dicts to a PyArrow Table using the fixed schema."""
    cols = {field.name: [] for field in PARQUET_SCHEMA}
    for r in rows:
        for field in PARQUET_SCHEMA:
            val = r.get(field.name)
            # Coerce types to match schema
            if pa.types.is_int32(field.type):
                val = int(val) if val is not None else None
            elif pa.types.is_string(field.type):
                val = str(val) if val is not None else None
            cols[field.name].append(val)

    arrays = [pa.array(cols[f.name], type=f.type) for f in PARQUET_SCHEMA]
    return pa.table(arrays, schema=PARQUET_SCHEMA)


# ---------------------------------------------------------------------------
# SQLite schema creation
# ---------------------------------------------------------------------------

DDL_DELIVERIES = """
CREATE TABLE IF NOT EXISTS deliveries (
    match_id        TEXT    NOT NULL,
    match_format    TEXT    NOT NULL,
    season          TEXT,
    date            TEXT,
    venue           TEXT,
    city            TEXT,
    gender          TEXT    NOT NULL,
    team1           TEXT,
    team2           TEXT,
    batting_team    TEXT,
    bowling_team    TEXT,
    innings         INTEGER,
    over            INTEGER,
    ball            INTEGER,
    batter          TEXT,
    non_striker     TEXT,
    bowler          TEXT,
    runs_batter     INTEGER DEFAULT 0,
    runs_extras     INTEGER DEFAULT 0,
    runs_total      INTEGER DEFAULT 0,
    is_wide         INTEGER DEFAULT 0,
    is_noball       INTEGER DEFAULT 0,
    wicket_kind     TEXT,
    player_out      TEXT,
    fielder         TEXT,
    toss_winner     TEXT,
    toss_decision   TEXT,
    result_winner   TEXT,
    result_margin   INTEGER,
    result_unit     TEXT
)
"""

DDL_MATCHES = """
CREATE TABLE IF NOT EXISTS matches (
    match_id         TEXT PRIMARY KEY,
    match_format     TEXT NOT NULL,
    season           TEXT,
    date             TEXT,
    venue            TEXT,
    city             TEXT,
    gender           TEXT NOT NULL,
    team1            TEXT,
    team2            TEXT,
    toss_winner      TEXT,
    toss_decision    TEXT,
    result           TEXT,
    result_winner    TEXT,
    result_margin    INTEGER,
    result_unit      TEXT,
    player_of_match  TEXT,
    overs_per_inns   INTEGER,
    total_deliveries INTEGER DEFAULT 0
)
"""

DDL_PLAYERS = """
CREATE TABLE IF NOT EXISTS players (
    match_id    TEXT NOT NULL,
    team        TEXT NOT NULL,
    player_name TEXT NOT NULL,
    PRIMARY KEY (match_id, team, player_name)
)
"""

INSERT_DELIVERY = """
INSERT INTO deliveries VALUES (
    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
)
"""

INSERT_MATCH = """
INSERT OR REPLACE INTO matches VALUES (
    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
)
"""

INSERT_PLAYER = """
INSERT OR IGNORE INTO players VALUES (?, ?, ?)
"""

INDEXES = [
    # 7 composite indexes optimised for the web query interface
    "CREATE INDEX IF NOT EXISTS idx_del_batter    ON deliveries(batter, match_format, date)",
    "CREATE INDEX IF NOT EXISTS idx_del_bowler    ON deliveries(bowler, match_format, date)",
    "CREATE INDEX IF NOT EXISTS idx_del_match     ON deliveries(match_id, innings, over)",
    "CREATE INDEX IF NOT EXISTS idx_del_format_dt ON deliveries(match_format, date)",
    "CREATE INDEX IF NOT EXISTS idx_del_teams     ON deliveries(batting_team, bowling_team)",
    "CREATE INDEX IF NOT EXISTS idx_del_wicket    ON deliveries(player_out, wicket_kind)",
    "CREATE INDEX IF NOT EXISTS idx_match_date    ON matches(date, match_format, team1, team2)",
]


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(dataset_dir: Path, db_path: Path, pq_path: Path) -> None:
    t0 = time.perf_counter()

    # ── Ensure output directory exists ──────────────────────────────────────
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Safety guard: refuse to overwrite protected legacy databases ─────────
    protected = {
        Path("cricket.db"),
        Path("cricket.db.bak"),
        Path("cricket_india.db"),
        Path("cricket_test.db"),
        Path("matches.parquet"),
        Path("matches.csv"),
    }
    if db_path in protected or pq_path in protected:
        sys.exit(
            "[ABORT] Target paths overlap with legacy files. "
            "Check DB_OUT_PATH / PQ_OUT_PATH."
        )

    # ── SQLite setup ─────────────────────────────────────────────────────────
    if db_path.exists():
        print(f"[INFO] Removing stale output database: {db_path}")
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous  = NORMAL;")
    cur.execute("PRAGMA cache_size   = -65536;")   # 64 MB page cache
    cur.execute("PRAGMA temp_store   = MEMORY;")
    cur.execute(DDL_DELIVERIES)
    cur.execute(DDL_MATCHES)
    cur.execute(DDL_PLAYERS)
    conn.commit()

    # ── Parquet writer setup ─────────────────────────────────────────────────
    pq_writer = None
    if HAS_PYARROW:
        if pq_path.exists():
            pq_path.unlink()
        pq_writer = pq.ParquetWriter(
            str(pq_path),
            schema=PARQUET_SCHEMA,
            compression="ZSTD",
        )

    # ── Accumulators ─────────────────────────────────────────────────────────
    delivery_batch: list = []
    match_rows:     list = []
    player_batch:   list = []

    stats = {
        "files_scanned":   0,
        "files_skipped":   0,
        "matches_loaded":  0,
        "deliveries_total": 0,
        "formats":         {"ODI": 0, "Test": 0},
        "discard_format":  0,
        "discard_date":    0,
        "discard_gender":  0,
        "discard_parse":   0,
    }

    json_files = sorted(dataset_dir.rglob("*.json"))
    total_files = len(json_files)
    print(f"[INFO] Source directory  : {dataset_dir}")
    print(f"[INFO] SQLite target     : {db_path}")
    print(f"[INFO] Parquet target    : {pq_path}")
    print(f"[INFO] Total JSON files  : {total_files:,}")
    print(f"[INFO] Allowed formats   : {sorted(ALLOWED_FORMATS)}")
    print(f"[INFO] Date window       : {DATE_MIN} → {DATE_MAX}")
    print(f"[INFO] Gender filter     : {REQUIRED_GENDER!r}")
    print()

    # ── Main ingestion loop ──────────────────────────────────────────────────
    for file_idx, json_file in enumerate(json_files, start=1):
        stats["files_scanned"] += 1

        # Progress heartbeat every 1000 files
        if file_idx % 1000 == 0 or file_idx == total_files:
            elapsed = time.perf_counter() - t0
            print(
                f"  [{file_idx:>6}/{total_files}]  "
                f"loaded={stats['matches_loaded']:>5}  "
                f"deliveries={stats['deliveries_total']:>9,}  "
                f"elapsed={elapsed:.1f}s"
            )

        # ── Parse JSON ───────────────────────────────────────────────────────
        try:
            with open(json_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            stats["discard_parse"] += 1
            stats["files_skipped"] += 1
            continue

        info = data.get("info", {})
        if not info:
            stats["files_skipped"] += 1
            continue

        # ── Filter 1: Match format ───────────────────────────────────────────
        match_type = info.get("match_type", "")
        if match_type not in ALLOWED_FORMATS:
            stats["discard_format"] += 1
            stats["files_skipped"] += 1
            continue

        # ── Filter 2: Gender ─────────────────────────────────────────────────
        gender = info.get("gender", "")
        if gender != REQUIRED_GENDER:
            stats["discard_gender"] += 1
            stats["files_skipped"] += 1
            continue

        # ── Filter 3: Date ───────────────────────────────────────────────────
        raw_dates = info.get("dates", [])
        first_date = _parse_date(raw_dates[0] if raw_dates else None)
        if first_date is None or not (DATE_MIN <= first_date <= DATE_MAX):
            stats["discard_date"] += 1
            stats["files_skipped"] += 1
            continue

        # ── All filters passed – extract core fields ─────────────────────────
        match_id     = json_file.stem
        season       = _safe_str(info.get("season"))
        date_str     = str(raw_dates[0])
        venue        = _safe_str(info.get("venue"))
        city         = _safe_str(info.get("city"))
        teams        = info.get("teams", [])
        team1        = _safe_str(teams[0]) if len(teams) > 0 else ""
        team2        = _safe_str(teams[1]) if len(teams) > 1 else ""
        toss         = info.get("toss", {})
        toss_winner  = _safe_str(toss.get("winner"))
        toss_decision= _safe_str(toss.get("decision"))

        outcome       = info.get("outcome", {})
        result_winner = outcome.get("winner")
        result_margin = None
        result_unit   = None
        by = outcome.get("by", {})
        if by:
            for unit, val in by.items():
                result_unit   = unit
                result_margin = int(val) if val is not None else None
                break

        # ── Innings / delivery extraction ────────────────────────────────────
        innings_list    = data.get("innings", [])
        match_deliveries= 0

        for inn_idx, inning in enumerate(innings_list):
            innings_num  = inn_idx + 1
            batting_team = inning.get("team")

            # Determine bowling team
            if batting_team == team1:
                bowling_team = team2
            elif batting_team == team2:
                bowling_team = team1
            else:
                others = [t for t in teams if t != batting_team]
                bowling_team = others[0] if others else None

            for over_data in inning.get("overs", []):
                over_num   = over_data.get("over")
                deliveries = over_data.get("deliveries", [])

                for ball_idx, delivery in enumerate(deliveries):
                    ball_num    = ball_idx + 1
                    batter      = delivery.get("batter")
                    non_striker = delivery.get("non_striker")
                    bowler      = delivery.get("bowler")

                    runs        = delivery.get("runs", {})
                    runs_batter = runs.get("batter",  0)
                    runs_extras = runs.get("extras",  0)
                    runs_total  = runs.get("total",   0)

                    # Extras flags (useful for bowling analysis)
                    extras_detail = delivery.get("extras", {})
                    is_wide   = 1 if "wides"  in extras_detail else 0
                    is_noball = 1 if "noballs" in extras_detail else 0

                    wickets     = delivery.get("wickets", [])
                    wicket_kind = None
                    player_out  = None
                    fielder     = None
                    if wickets:
                        w           = wickets[0]
                        wicket_kind = w.get("kind")
                        player_out  = w.get("player_out")
                        fielders    = w.get("fielders", [])
                        if fielders:
                            fielder = fielders[0].get("name")

                    delivery_batch.append((
                        match_id, match_type, season, date_str, venue, city, gender,
                        team1, team2,
                        batting_team, bowling_team,
                        innings_num, over_num, ball_num,
                        batter, non_striker, bowler,
                        runs_batter, runs_extras, runs_total,
                        is_wide, is_noball,
                        wicket_kind, player_out, fielder,
                        toss_winner, toss_decision,
                        result_winner, result_margin, result_unit,
                    ))
                    match_deliveries += 1

                    # Flush delivery batch to SQLite
                    if len(delivery_batch) >= DELIVERY_BATCH_SIZE:
                        cur.executemany(INSERT_DELIVERY, delivery_batch)
                        conn.commit()
                        delivery_batch = []

        # ── Players table rows ────────────────────────────────────────────────
        registry = info.get("players", {})          # {team: [player, ...], ...}
        for team_name, player_list in registry.items():
            for pname in player_list:
                player_batch.append((match_id, team_name, pname))

        # Flush players
        if len(player_batch) >= 5_000:
            cur.executemany(INSERT_PLAYER, player_batch)
            conn.commit()
            player_batch = []

        # ── Matches table row ─────────────────────────────────────────────────
        cur.execute(INSERT_MATCH, (
            match_id, match_type, season, date_str, venue, city, gender,
            team1, team2,
            toss_winner, toss_decision,
            outcome.get("result"),
            result_winner, result_margin, result_unit,
            ", ".join(info.get("player_of_match", [])) or None,
            info.get("overs"),           # None for Tests
            match_deliveries,
        ))

        # ── Parquet match row buffer ──────────────────────────────────────────
        if HAS_PYARROW:
            match_rows.append(_extract_match_row(match_id, info, match_deliveries))
            if len(match_rows) >= MATCH_ROW_BUFFER:
                pq_writer.write_table(_schema_row_to_arrays(match_rows))
                match_rows = []

        stats["matches_loaded"]   += 1
        stats["deliveries_total"] += match_deliveries
        stats["formats"][match_type] = stats["formats"].get(match_type, 0) + 1

    # ── Final flushes ────────────────────────────────────────────────────────
    if delivery_batch:
        cur.executemany(INSERT_DELIVERY, delivery_batch)
    if player_batch:
        cur.executemany(INSERT_PLAYER, player_batch)
    conn.commit()

    if HAS_PYARROW and match_rows:
        pq_writer.write_table(_schema_row_to_arrays(match_rows))

    if HAS_PYARROW and pq_writer:
        pq_writer.close()

    # ── Build 7 composite indexes ────────────────────────────────────────────
    print("\n[INFO] Building composite indexes …")
    for idx_sql in INDEXES:
        idx_name = idx_sql.split("idx_")[1].split(" ")[0]
        print(f"  → {idx_name}")
        cur.execute(idx_sql)
    conn.commit()
    conn.close()

    # ── Final report ─────────────────────────────────────────────────────────
    elapsed     = time.perf_counter() - t0
    db_size_mb  = db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0
    pq_size_mb  = pq_path.stat().st_size / (1024 * 1024) if (HAS_PYARROW and pq_path.exists()) else 0

    print()
    print("┌─────────────────────────────────────────────────────────────┐")
    print("│           cricket_clean  ·  Ingestion Report                │")
    print("├──────────────────────────────────┬──────────────────────────┤")
    print(f"│  Source directory                │  {str(dataset_dir):<24}│")
    print(f"│  Files scanned                   │  {stats['files_scanned']:<24,}│")
    print(f"│  Files skipped (all filters)     │  {stats['files_skipped']:<24,}│")
    print(f"│    → discarded (format)          │  {stats['discard_format']:<24,}│")
    print(f"│    → discarded (date)            │  {stats['discard_date']:<24,}│")
    print(f"│    → discarded (gender)          │  {stats['discard_gender']:<24,}│")
    print(f"│    → discarded (parse error)     │  {stats['discard_parse']:<24,}│")
    print(f"│  Matches loaded                  │  {stats['matches_loaded']:<24,}│")
    fmt_str = f"ODI={stats['formats'].get('ODI',0):,}  Test={stats['formats'].get('Test',0):,}"
    print(f"│  Format breakdown                │  {fmt_str:<24}│")
    print(f"│  Total deliveries                │  {stats['deliveries_total']:<24,}│")
    print(f"│  SQLite path                     │  {str(db_path):<24}│")
    print(f"│  SQLite size                     │  {db_size_mb:<23.1f} MB│")
    if HAS_PYARROW:
        print(f"│  Parquet path                    │  {str(pq_path):<24}│")
        print(f"│  Parquet size (ZSTD)             │  {pq_size_mb:<23.1f} MB│")
    else:
        print(f"│  Parquet output                  │  {'SKIPPED (no pyarrow)':<24}│")
    print(f"│  Elapsed time                    │  {elapsed:<23.1f} s│")
    print("└──────────────────────────────────┴──────────────────────────┘")
    print()
    print("[DONE] cricket_clean pipeline completed successfully.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Extract clean international cricket data (ODI + Test, male, 2019-2026).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dataset-path",
        default=str(SOURCE_DIR),
        help=f"Path to Cricsheet JSON match directory (default: {SOURCE_DIR})",
    )
    parser.add_argument(
        "--db-path",
        default=str(DB_OUT_PATH),
        help=f"SQLite output path (default: {DB_OUT_PATH})",
    )
    parser.add_argument(
        "--pq-path",
        default=str(PQ_OUT_PATH),
        help=f"Parquet output path (default: {PQ_OUT_PATH})",
    )
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_path)
    db_path     = Path(args.db_path)
    pq_path     = Path(args.pq_path)

    if not dataset_dir.exists() or not dataset_dir.is_dir():
        sys.exit(f"[ERROR] Source directory not found: {dataset_dir}")

    try:
        run_pipeline(dataset_dir, db_path, pq_path)
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Pipeline cancelled by user.")
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

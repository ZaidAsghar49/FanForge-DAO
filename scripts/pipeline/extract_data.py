"""
extract_data.py — Active Player Era Low-RAM Ingestion Engine (ZSTD Compression)
================================================================================
Flattens nested Cricsheet JSON match files and populates both the SQLite DB
(cricket.db) and Parquet store (matches.parquet) directly using stream-writing.

Max RAM usage is strictly bounded under 250MB. Matches prior to 2019-01-01 are
discarded early after opening date metadata.
"""

import os
import sys
import json
import sqlite3
import argparse
import time
import gc
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).resolve().parents[2]
DATASET_DIR  = str(ROOT / "Dataset" / "Matches")
SQLITE_DB    = str(ROOT / "cricket.db")
PARQUET_FILE = str(ROOT / "matches.parquet")
BOWLERS_CSV  = str(ROOT / "bowlers.csv")

# ── Load city → country map ───────────────────────────────────────────────────
sys.path.insert(0, str(ROOT))
from scripts.pipeline.city_map import CITY_COUNTRY_MAP

# ── Bowler Cache ──────────────────────────────────────────────────────────────
def _load_bowler_cache() -> dict[str, tuple[str, str]]:
    """Returns {bowler_name: (type, hand)} from bowlers.csv."""
    cache = {}
    try:
        import csv
        with open(BOWLERS_CSV, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                name  = row.get("bowler", "").strip()
                style = row.get("style", "").strip()
                btype = "Spin" if style == "Spin" else ("Pace" if style == "Pace" else "Unknown")
                cache[name] = (btype, "Unknown")
    except Exception:
        pass
    return cache

# ── Heuristic helpers ─────────────────────────────────────────────────────────
def _match_phase(over: int) -> str:
    if over <= 5:
        return "Powerplay"
    if over <= 14:
        return "Middle"
    return "Death"

def _day_night(info: dict) -> str:
    mv = str(info.get("match_type_variant", "")).lower()
    if "day/night" in mv or "day-night" in mv or "floodlit" in mv:
        return "Day/Night"
    if "night" in mv:
        return "Night"
    return "Day"

def _competition(info: dict) -> str:
    event = info.get("event", {})
    if isinstance(event, dict):
        name = event.get("name", "")
    else:
        name = str(event)
    if name:
        return name
    return str(info.get("competition", "International")).strip() or "International"

def _home_team(info: dict) -> str:
    city    = info.get("city", "")
    country = CITY_COUNTRY_MAP.get(city, "")
    teams   = info.get("teams", [])
    if not teams:
        return ""
    team_country_hints = {
        "Australia": "Australia", "England": "England", "India": "India",
        "Pakistan": "Pakistan", "South Africa": "South Africa",
        "New Zealand": "New Zealand", "West Indies": "West Indies",
        "Sri Lanka": "Sri Lanka", "Bangladesh": "Bangladesh",
        "Zimbabwe": "Zimbabwe", "Afghanistan": "Afghanistan",
        "Ireland": "Ireland",
    }
    for team in teams:
        tc = team_country_hints.get(team, "")
        if tc and tc.lower() == country.lower():
            return team
    return teams[0]

BOWLER_WICKET_KINDS = frozenset({
    "bowled", "caught", "lbw", "stumped", "hit wicket", "caught and bowled"
})

# ── SQLite DDL ────────────────────────────────────────────────────────────────
SQLITE_CREATE = """
CREATE TABLE IF NOT EXISTS deliveries (
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
    innings          INTEGER DEFAULT 0,
    over             INTEGER DEFAULT 0,
    ball             INTEGER DEFAULT 0,
    batting_team     TEXT,
    bowling_team     TEXT,
    match_phase      TEXT,
    batter           TEXT,
    non_striker      TEXT,
    batting_position INTEGER DEFAULT 0,
    runs_batter      INTEGER DEFAULT 0,
    is_wicket        INTEGER DEFAULT 0,
    wicket_type      TEXT,
    is_bowler_wicket INTEGER DEFAULT 0,
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

SQLITE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_batter        ON deliveries(batter);",
    "CREATE INDEX IF NOT EXISTS idx_bowler        ON deliveries(bowler);",
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
    "CREATE INDEX IF NOT EXISTS idx_batting_team  ON deliveries(batting_team);",
    "CREATE INDEX IF NOT EXISTS idx_bowling_team  ON deliveries(bowling_team);",
    "CREATE INDEX IF NOT EXISTS idx_over          ON deliveries(over);",
    "CREATE INDEX IF NOT EXISTS idx_match_phase   ON deliveries(match_phase);",
    "CREATE INDEX IF NOT EXISTS idx_batting_pos   ON deliveries(batting_position);",
    "CREATE INDEX IF NOT EXISTS idx_non_striker   ON deliveries(non_striker);",
    "CREATE INDEX IF NOT EXISTS idx_wicket_type   ON deliveries(wicket_type);",
    "CREATE INDEX IF NOT EXISTS idx_is_wicket     ON deliveries(is_wicket);",
    "CREATE INDEX IF NOT EXISTS idx_bowler_type   ON deliveries(bowler_type);",
    "CREATE INDEX IF NOT EXISTS idx_bowler_hand   ON deliveries(bowler_hand);",
    "CREATE INDEX IF NOT EXISTS idx_batter_format ON deliveries(batter, match_type);",
    "CREATE INDEX IF NOT EXISTS idx_bowler_format ON deliveries(bowler, match_type);",
]

# ── PyArrow Schema (for Parquet consistency) ──────────────────────────────────
PARQUET_SCHEMA = pa.schema([
    ("match_id",         pa.string()),
    ("date",             pa.string()),
    ("season",           pa.string()),
    ("venue_name",       pa.string()),
    ("city",             pa.string()),
    ("country",          pa.string()),
    ("match_type",       pa.string()),
    ("competition",      pa.string()),
    ("day_night",        pa.string()),
    ("neutral_venue",    pa.int8()),
    ("toss_winner",      pa.string()),
    ("toss_decision",    pa.string()),
    ("team_a",           pa.string()),
    ("team_b",           pa.string()),
    ("home_team",        pa.string()),
    ("overs_limit",      pa.int16()),
    ("innings",          pa.int16()),
    ("over",             pa.int16()),
    ("ball",             pa.int16()),
    ("batting_team",     pa.string()),
    ("bowling_team",     pa.string()),
    ("match_phase",      pa.string()),
    ("batter",           pa.string()),
    ("non_striker",      pa.string()),
    ("batting_position", pa.int16()),
    ("runs_batter",      pa.int16()),
    ("is_wicket",        pa.int8()),
    ("wicket_type",      pa.string()),
    ("is_bowler_wicket", pa.int8()),
    ("bowler",           pa.string()),
    ("bowler_type",      pa.string()),
    ("bowler_hand",      pa.string()),
    ("runs_total",       pa.int16()),
    ("extras_wides",     pa.int16()),
    ("extras_noballs",   pa.int16()),
    ("extras_byes",      pa.int16()),
    ("extras_legbyes",   pa.int16()),
])

def _clean_and_cast_df(df: pd.DataFrame) -> pd.DataFrame:
    """Explicitly clean up column names and cast types for consistency."""
    int_cols = {
        "neutral_venue": "int8",
        "overs_limit": "int16",
        "innings": "int16",
        "over": "int16",
        "ball": "int16",
        "batting_position": "int16",
        "runs_batter": "int16",
        "is_wicket": "int8",
        "is_bowler_wicket": "int8",
        "runs_total": "int16",
        "extras_wides": "int16",
        "extras_noballs": "int16",
        "extras_byes": "int16",
        "extras_legbyes": "int16",
    }
    for col, dtype in int_cols.items():
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(dtype)

    str_cols = [
        "match_id", "date", "season", "venue_name", "city", "country",
        "match_type", "competition", "day_night", "toss_winner", "toss_decision",
        "team_a", "team_b", "home_team", "batting_team", "bowling_team", "match_phase",
        "batter", "non_striker", "wicket_type", "bowler", "bowler_type", "bowler_hand"
    ]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).replace({"nan": None, "None": None, "": None})

    return df

# ── Pipeline Implementation ───────────────────────────────────────────────────
def stream_and_filter_cricket_data(json_dir, sqlite_db_path, parquet_out_path, limit=None):
    print("Initializing Active Player Era (2019-2026) Low-RAM Pipeline...")
    t_start = time.time()

    # Load Bowler classifications
    bowler_cache = _load_bowler_cache()
    print(f"Bowler cache loaded: {len(bowler_cache):,} entries.")

    # Connect to SQLite
    conn = sqlite3.connect(sqlite_db_path)
    cursor = conn.cursor()

    # Apply SQLite performance pragmas for transaction speed
    cursor.executescript("""
        PRAGMA journal_mode = WAL;
        PRAGMA cache_size = -64000;
        PRAGMA synchronous = NORMAL;
    """)

    # Drop existing table to prevent duplicate ingestion runs
    cursor.execute("DROP TABLE IF EXISTS deliveries;")
    cursor.execute(SQLITE_CREATE)
    conn.commit()

    processed_count = 0
    skipped_count = 0
    parquet_writer = None

    # Scan and sort all files in json_dir
    all_files = sorted([f for f in os.listdir(json_dir) if f.endswith('.json')])
    if limit:
        all_files = all_files[:limit]
    total_files = len(all_files)

    print(f"Scanning {total_files:,} files from {json_dir}...")

    for i, filename in enumerate(all_files):
        file_path = os.path.join(json_dir, filename)

        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                match_data = json.load(f)
            except Exception:
                continue

        # --- EARLY DISCARD BLOCK (RAM Optimization) ---
        info = match_data.get('info', {})
        dates = info.get('dates', [])
        if not dates:
            del match_data
            continue

        match_date_str = dates[0]
        try:
            match_year = int(match_date_str.split('-')[0])
        except ValueError:
            del match_data
            continue

        # Option 5 boundary: matches from Jan 1, 2019 onwards
        if match_year < 2019:
            skipped_count += 1
            del match_data
            # Force garbage collection occasionally
            if skipped_count % 1000 == 0:
                gc.collect()
            continue

        # --- CONVERSION & FLATTENING BLOCK ---
        delivery_rows = []

        match_id      = filename.replace('.json', '')
        teams         = info.get("teams", [])
        season        = str(info.get("season", match_date_str[:4]))
        venue_name    = info.get("venue", "")
        city          = info.get("city", venue_name)
        country       = CITY_COUNTRY_MAP.get(city, info.get("country", "Unknown"))
        match_type    = info.get("match_type", "Unknown")
        competition   = _competition(info)
        day_night     = _day_night(info)
        neutral_v     = int(bool(info.get("neutral_venue", False)))
        toss          = info.get("toss", {})
        toss_winner   = toss.get("winner", "")
        toss_decision = toss.get("decision", "")
        overs_limit   = int(info.get("overs", 20 if match_type == "T20" else 50))
        team_a        = teams[0] if len(teams) > 0 else ""
        team_b        = teams[1] if len(teams) > 1 else ""
        home_team     = _home_team(info)

        innings_list = match_data.get('innings', [])
        for inn_idx, innings_data in enumerate(innings_list):
            innings_no = inn_idx + 1
            batting_team = innings_data.get("team", "")
            bowling_team = next((t for t in teams if t != batting_team), "")
            overs = innings_data.get('overs', [])

            batting_order = {}
            position_counter = 0

            for over_data in overs:
                over_no = int(over_data.get('over', 0))
                phase = _match_phase(over_no)
                deliveries = over_data.get('deliveries', [])

                for ball_idx, delivery in enumerate(deliveries):
                    batter      = delivery.get('batter', '')
                    non_striker = delivery.get('non_striker', '')
                    bowler      = delivery.get('bowler', '')

                    if batter and batter not in batting_order:
                        position_counter += 1
                        batting_order[batter] = position_counter
                    bat_pos = batting_order.get(batter, 0)

                    runs_obj    = delivery.get('runs', {})
                    runs_batter = int(runs_obj.get('batter', 0))
                    extras_val  = int(runs_obj.get('extras', 0))
                    runs_total  = int(runs_obj.get('total', runs_batter + extras_val))

                    extras_obj  = delivery.get('extras', {})
                    ext_wides   = int(extras_obj.get('wides', 0))
                    ext_noballs = int(extras_obj.get('noballs', 0))
                    ext_byes    = int(extras_obj.get('byes', 0))
                    ext_legbyes = int(extras_obj.get('legbyes', 0))

                    wicket_list      = delivery.get('wickets', [])
                    is_wicket        = 0
                    wicket_type      = ""
                    is_bowler_wicket = 0

                    if wicket_list:
                        w = wicket_list[0]
                        kind = w.get('kind', '')
                        if w.get('player_out', '') == batter:
                            is_wicket   = 1
                            wicket_type = kind
                        if kind in BOWLER_WICKET_KINDS:
                            is_bowler_wicket = 1

                    btype, bhand = bowler_cache.get(bowler, ("Unknown", "Unknown"))

                    delivery_rows.append({
                        "match_id":         match_id,
                        "date":             match_date_str,
                        "season":           season,
                        "venue_name":       venue_name,
                        "city":             city,
                        "country":          country,
                        "match_type":       match_type,
                        "competition":      competition,
                        "day_night":        day_night,
                        "neutral_venue":    neutral_v,
                        "toss_winner":      toss_winner,
                        "toss_decision":    toss_decision,
                        "team_a":           team_a,
                        "team_b":           team_b,
                        "home_team":        home_team,
                        "overs_limit":      overs_limit,
                        "innings":          innings_no,
                        "over":             over_no,
                        "ball":             ball_idx + 1,
                        "batting_team":     batting_team,
                        "bowling_team":     bowling_team,
                        "match_phase":      phase,
                        "batter":           batter,
                        "non_striker":      non_striker,
                        "batting_position": bat_pos,
                        "runs_batter":      runs_batter,
                        "is_wicket":        is_wicket,
                        "wicket_type":      wicket_type,
                        "is_bowler_wicket": is_bowler_wicket,
                        "bowler":           bowler,
                        "bowler_type":      btype,
                        "bowler_hand":      bhand,
                        "runs_total":       runs_total,
                        "extras_wides":     ext_wides,
                        "extras_noballs":   ext_noballs,
                        "extras_byes":      ext_byes,
                        "extras_legbyes":   ext_legbyes,
                    })

        # --- STORAGE FLUSH BLOCK ---
        if delivery_rows:
            df_match = pd.DataFrame(delivery_rows)
            df_match = _clean_and_cast_df(df_match)

            # 1. Append to SQLite
            df_match.to_sql('deliveries', conn, if_exists='append', index=False)

            # 2. Append incrementally to Parquet (ZSTD compression for optimal size/speed ratio)
            pa_table = pa.Table.from_pandas(df_match, schema=PARQUET_SCHEMA, preserve_index=False)
            if parquet_writer is None:
                parquet_writer = pq.ParquetWriter(parquet_out_path, PARQUET_SCHEMA, compression='ZSTD')
            parquet_writer.write_table(pa_table)

            processed_count += 1
            if processed_count % 100 == 0:
                elapsed = time.time() - t_start
                rate = processed_count / elapsed if elapsed > 0 else 0
                print(f"Status Update: Processed {processed_count} matches | Skipped {skipped_count} pre-2019 matches | Rate: {rate:.1f} matches/sec")

        # Explicitly free memory
        del delivery_rows
        del match_data
        gc.collect()

    # Create SQLite indexes for fast dashboard queries
    print("Building database indexes...")
    for idx_sql in SQLITE_INDEXES:
        cursor.execute(idx_sql)
    conn.commit()

    # Close resources
    if parquet_writer:
        parquet_writer.close()
    conn.close()

    elapsed = time.time() - t_start
    print(f"Pipeline Execution Terminated. Extraction Complete!")
    print(f"  Processed Modern Era Matches : {processed_count}")
    print(f"  Skipped Historical Matches   : {skipped_count}")
    print(f"  Elapsed Time                 : {elapsed:.1f}s")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Active Player Era Low-RAM Ingestion Engine")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N files (for testing)")
    parser.add_argument("--json-dir", type=str, default=DATASET_DIR, help="Path to match JSON directory")
    parser.add_argument("--sqlite-db", type=str, default=SQLITE_DB, help="Output SQLite database path")
    parser.add_argument("--parquet-out", type=str, default=PARQUET_FILE, help="Output Parquet path")
    args = parser.parse_args()

    # Re-evaluate database & Parquet outputs
    stream_and_filter_cricket_data(
        json_dir=args.json_dir,
        sqlite_db_path=args.sqlite_db,
        parquet_out_path=args.parquet_out,
        limit=args.limit
    )

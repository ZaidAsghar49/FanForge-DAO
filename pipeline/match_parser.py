import json
import os
import duckdb
import logging
from pathlib import Path
import pandas as pd
from datetime import datetime
import time

# Configure logging specifically for the parser
logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]

class MatchParser:
    def __init__(self, bowler_cache=None):
        from scripts.identity.identity_engine import IdentityEngine
        self.engine = IdentityEngine()
        self.bowler_cache = bowler_cache or {}
        self.batter_hand_cache = {}
        self.bowler_style_cache = {}
        self.bowler_wicket_kinds = frozenset({
            "bowled", "caught", "lbw", "stumped", "hit wicket", "caught and bowled"
        })

    def _match_phase(self, over: int, match_type: str, overs_limit: int | None):
        """
        Format-aware phases:
        - T20/T20I: Powerplay overs 1–6, Death = last 5 overs
        - ODI:      Powerplay overs 1–10, Death = last 5 overs
        - Fallback: coarse buckets
        NOTE: over is 0-indexed (Cricsheet).
        """
        mt = (match_type or "").lower()
        ol = None
        try:
            ol = int(overs_limit) if overs_limit is not None else None
        except Exception:
            ol = None
        if ol is None:
            ol = 50 if "odi" in mt else 20 if "t20" in mt else None

        if ol is not None and over >= max(0, ol - 5):
            return "Death"
        if "odi" in mt:
            return "Powerplay" if over <= 9 else "Middle"
        if "t20" in mt:
            return "Powerplay" if over <= 5 else "Middle"
        if over <= 5:
            return "Powerplay"
        if over <= 14:
            return "Middle"
        return "Death"

    def parse_match(self, filepath):
        """Parses a single Cricsheet JSON match file into structured rows."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Error reading {filepath}: {e}")
            return []

        info = data.get("info", {})
        innings = data.get("innings", [])
        if not innings: return []

        match_id = os.path.basename(filepath).split(".")[0]
        teams = info.get("teams", [])
        dates = info.get("dates", [datetime.now().strftime("%Y-%m-%d")])
        date_str = dates[0]
        season = str(info.get("season", date_str[:4]))
        venue = info.get("venue", "Unknown")
        city = info.get("city", venue)
        match_type = info.get("match_type", "Unknown")
        
        # New Match-Level Info
        toss = info.get("toss", {})
        toss_winner = toss.get("winner", "Unknown")
        toss_decision = toss.get("decision", "Unknown")
        team_a = teams[0] if len(teams) > 0 else "Unknown"
        team_b = teams[1] if len(teams) > 1 else "Unknown"
        event = info.get("event", {})
        competition = event.get("name", match_type)
        overs_limit = info.get("overs", 20)
        country = info.get("country", "Unknown")
        
        # Normalize day/night to stable strings so filters work reliably.
        # Cricsheet may provide a boolean-like flag; we convert to "day" / "day-night".
        is_day_night = bool(info.get("day_night"))
        day_night = "day-night" if is_day_night else "day"
        ball_type = "pink" if (match_type == "Test" and is_day_night) else "red"

        records = []
        for inn_idx, inning in enumerate(innings):
            inn_num = inn_idx + 1
            bat_team = inning.get("team", "")
            bowl_team = next((t for t in teams if t != bat_team), "Unknown")
            
            batting_order = {}
            pos_counter = 0
            
            for over_obj in inning.get("overs", []):
                over_num = int(over_obj.get("over", 0))
                phase = self._match_phase(over_num, match_type, overs_limit)
                
                for ball_idx, delivery in enumerate(over_obj.get("deliveries", [])):
                    batter = delivery.get("batter", "")
                    bowler = delivery.get("bowler", "")
                    non_striker = delivery.get("non_striker", "")
                    
                    if batter and batter not in batting_order:
                        pos_counter += 1
                        batting_order[batter] = pos_counter
                        # Pre-cache hand for this match
                        if batter not in self.batter_hand_cache:
                            res = self.engine.resolve_for_ingestion(batter, bat_team)
                            if res:
                                # Prefer canonical hand derived by IdentityEngine
                                h = res.get("batter_hand")
                                if not h or h == "Unknown":
                                    s = str(res.get("batting_style", "")).lower()
                                    h = "Left" if "left" in s else ("Right" if "right" in s else None)
                                self.batter_hand_cache[batter] = h
                            else:
                                self.batter_hand_cache[batter] = None
                    bat_pos = batting_order.get(batter, 0)
                    hand = self.batter_hand_cache.get(batter)

                    # Cache bowler type/hand once per match
                    if bowler and bowler not in self.bowler_style_cache:
                        bres = self.engine.resolve_for_ingestion(bowler, bowl_team)
                        if bres:
                            self.bowler_style_cache[bowler] = (
                                bres.get("bowling_type"),
                                bres.get("bowler_hand"),
                            )
                        else:
                            self.bowler_style_cache[bowler] = (None, None)
                    bow_type, bow_hand = self.bowler_style_cache.get(bowler, (None, None))
                    
                    runs = delivery.get("runs", {})
                    rb = int(runs.get("batter", 0))
                    re = int(runs.get("extras", 0))
                    rt = int(runs.get("total", rb + re))
                    
                    extras = delivery.get("extras", {})
                    ew = int(extras.get("wides", 0))
                    en = int(extras.get("noballs", 0))
                    eb = int(extras.get("byes", 0))
                    el = int(extras.get("legbyes", 0))
                    
                    is_wicket = 0
                    wicket_type = ""
                    is_bowler_wicket = 0
                    
                    for w in delivery.get("wickets", []):
                        kind = w.get("kind", "")
                        if w.get("player_out") == batter:
                            is_wicket = 1
                            wicket_type = kind
                        if kind in self.bowler_wicket_kinds:
                            is_bowler_wicket = 1
                            
                    records.append({
                        "match_id": match_id,
                        "date": date_str,
                        "season": season,
                        "venue_name": venue,
                        "city": city,
                        "country": country,
                        "match_type": match_type,
                        "competition": competition,
                        "day_night": day_night,
                        "ball_type": ball_type,

                        "neutral_venue": 1 if info.get("neutral_venue") else 0,
                        "toss_winner": toss_winner,
                        "toss_decision": toss_decision,
                        "team_a": team_a,
                        "team_b": team_b,
                        "home_team": team_a, # Often first team is home or neutral
                        "overs_limit": overs_limit,
                        "innings": inn_num,
                        "over": over_num,
                        "ball": ball_idx + 1,
                        "batting_team": bat_team,
                        "bowling_team": bowl_team,
                        "match_phase": phase,
                        "batter": batter,
                        "non_striker": non_striker,
                        "batting_position": bat_pos,
                        "runs_batter": rb,
                        "is_wicket": is_wicket,
                        "wicket_type": wicket_type,
                        "is_bowler_wicket": is_bowler_wicket,
                        "bowler": bowler,
                        "bowler_type": bow_type,
                        "bowler_hand": bow_hand,
                        "batter_hand": hand,
                        "runs_total": rt,
                        "extras_wides": ew,
                        "extras_noballs": en,
                        "extras_byes": eb,
                        "extras_legbyes": el
                    })
        return records

def get_hardened_connection(db_path):
    """Returns a DuckDB connection with optimized settings for high-performance ingestion."""
    # DuckDB path usually ends in .duckdb
    if db_path.endswith(".db"):
        db_path = db_path.replace(".db", ".duckdb")
        # Ensure it's in the processed folder if not absolute
        if not os.path.isabs(db_path) and "data" not in db_path:
            db_path = os.path.join("data", "processed", db_path)
    
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = duckdb.connect(db_path)
    con.execute("SET preserve_insertion_order=false") # Faster inserts
    return con

def optimize_database(db_path):
    """Creates indexes and runs vacuum/analyze to optimize for analytics."""
    logger.info("[*] Optimizing DuckDB database...")
    con = get_hardened_connection(db_path)
    try:
        # DuckDB indexes are different but we can still create them if needed for point queries
        # though DuckDB is mostly columnar.
        con.execute("CREATE INDEX IF NOT EXISTS idx_batter ON deliveries (batter)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_bowler ON deliveries (bowler)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_match_id ON deliveries (match_id)")
        
        logger.info("[*] Running CHECKPOINT to ensure data is persisted...")
        con.execute("CHECKPOINT")
    except Exception as e:
        logger.error(f"Optimization error: {e}")
    finally:
        con.close()

def process_new_matches(raw_dir, db_path, log_file):
    """
    Orchestrates the continuous ingestion of new files.
    Implements streaming parsing and chunked batch insertion for stability.
    """
    import glob
    start_time = time.time()
    
    RAW_DIR = Path(raw_dir)
    LOG_FILE = Path(log_file)
    
    # 1. Fault Tolerance: Resume from checkpoint
    def _atomic_write_json(path: Path, obj) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        tmp.replace(path)

    processed = set()
    if LOG_FILE.exists():
        try:
            raw = LOG_FILE.read_text(encoding="utf-8").strip()
            # Guard against accidental multiple JSON arrays concatenated in the file.
            # Keep only the last complete JSON array.
            if raw.count("[") > 1:
                raw = raw[raw.rfind("[") :]
            processed = set(json.loads(raw)) if raw else set()
        except Exception:
            logger.warning("[!] Log file corrupted, starting fresh.")
            
    all_json = glob.glob(str(RAW_DIR / "*.json"))
    new_files = [f for f in all_json if os.path.basename(f) not in processed]
    
    if not new_files:
        logger.info("[*] Everything is up to date.")
        return 0

    new_files = sorted(new_files)
    logger.info(f"[*] Ingestion starting: {len(new_files)} new matches detected.")
    parser = MatchParser()
    
    # Streaming Configuration
    DELIVERY_BUFFER = []
    FLUSH_THRESHOLD = 50000 # Flush every 50k deliveries (~200 matches)
    matches_ingested = 0
    total_deliveries = 0
    total_inserted = 0

    # Live status output (monitoring)
    STATUS_DIR = ROOT / "output"
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_FILE = STATUS_DIR / "duckdb_ingestion_status.json"

    def _write_status(state: dict) -> None:
        try:
            STATUS_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            # Status output must never crash ingestion
            pass

    con = get_hardened_connection(db_path)
    
    # Required Column Order for Deliveries Table (based on current DuckDB schema)
    COLUMN_ORDER = [
        'match_id', 'date', 'season', 'venue_name', 'city', 'country', 'match_type', 
        'competition', 'day_night', 'neutral_venue', 'ball_type', 'toss_winner', 'toss_decision', 
        'team_a', 'team_b', 'home_team', 'overs_limit', 'innings', 'over', 'ball', 
        'batting_team', 'bowling_team', 'match_phase', 'batter', 'non_striker', 
        'batting_position', 'runs_batter', 'is_wicket', 'wicket_type', 'is_bowler_wicket', 
        'bowler', 'bowler_type', 'bowler_hand', 'batter_hand', 'runs_total', 'extras_wides', 
        'extras_noballs', 'extras_byes', 'extras_legbyes'
    ]
    
    try:
        for idx, f in enumerate(new_files):
            recs = parser.parse_match(f)
            if recs:
                DELIVERY_BUFFER.extend(recs)
                total_deliveries += len(recs)
            
            # 2. Streaming: Periodic Flush
            if len(DELIVERY_BUFFER) >= FLUSH_THRESHOLD or idx == len(new_files) - 1:
                if not DELIVERY_BUFFER:
                    continue
                    
                batch_df = pd.DataFrame(DELIVERY_BUFFER)
                
                # Align columns with table schema
                for col in COLUMN_ORDER:
                    if col not in batch_df.columns:
                        batch_df[col] = None
                batch_df = batch_df[COLUMN_ORDER]
                
                # 3. De-dup safeguard:
                # - Within the current batch (parser/merge artifacts)
                # - Against existing DB rows (re-runs / partial ingestion)
                #
                # A delivery is uniquely identified by match_id + innings + over + ball.
                dedup_key = ["match_id", "innings", "over", "ball"]
                for k in dedup_key:
                    if k not in batch_df.columns:
                        batch_df[k] = None
                batch_df = batch_df.drop_duplicates(subset=dedup_key, keep="first")

                # DuckDB Fast Batch Insertion:
                # Use DuckDB's ability to query pandas dataframes directly.
                con.execute("CREATE TABLE IF NOT EXISTS deliveries AS SELECT * FROM batch_df WHERE 1=0")
                con.execute(
                    """
                    INSERT INTO deliveries
                    SELECT b.*
                    FROM batch_df b
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM deliveries d
                        WHERE d.match_id = b.match_id
                          AND d.innings  = b.innings
                          AND d.over     = b.over
                          AND d.ball     = b.ball
                    )
                    """
                )
                # DuckDB doesn't support SQLite's changes(); keep best-effort counters.
                # total_inserted is approximate: assume all parsed rows were inserted after de-dup.
                total_inserted += len(batch_df)
                
                # Update tracking and log
                for processed_idx in range(matches_ingested, idx + 1):
                    processed.add(os.path.basename(new_files[processed_idx]))
                
                _atomic_write_json(LOG_FILE, list(processed))
                
                matches_ingested = idx + 1
                elapsed_s = max(1e-6, time.time() - start_time)
                rate_mps = matches_ingested / elapsed_s
                eta_s = (len(new_files) - matches_ingested) / rate_mps if rate_mps > 0 else None
                logger.info(
                    f"    [PROGRESS] Matches: {matches_ingested}/{len(new_files)} | "
                    f"Parsed deliveries: {total_deliveries:,} | Inserted: {total_inserted:,} | "
                    f"Rate: {rate_mps:.2f} matches/s | ETA: {eta_s/60:.1f} min" if eta_s else
                    f"    [PROGRESS] Matches: {matches_ingested}/{len(new_files)} | "
                    f"Parsed deliveries: {total_deliveries:,} | Inserted: {total_inserted:,} | "
                    f"Rate: {rate_mps:.2f} matches/s"
                )
                _write_status(
                    {
                        "status": "running",
                        "raw_dir": str(RAW_DIR),
                        "db_path": str(db_path),
                        "total_matches_target": len(new_files),
                        "matches_processed": matches_ingested,
                        "deliveries_parsed_total": total_deliveries,
                        "deliveries_inserted_total": total_inserted,
                        "last_match_file": os.path.basename(f),
                        "elapsed_seconds": round(elapsed_s, 3),
                        "rate_matches_per_sec": round(rate_mps, 4),
                        "eta_seconds": None if eta_s is None else round(eta_s, 1),
                        "updated_at_unix": time.time(),
                    }
                )
                DELIVERY_BUFFER = [] # Clear buffer
                
    except Exception as e:
        logger.error(f"Critical Ingestion Failure: {e}")
        _write_status(
            {
                "status": "error",
                "error": str(e),
                "raw_dir": str(RAW_DIR),
                "db_path": str(db_path),
                "matches_processed": matches_ingested,
                "deliveries_parsed_total": total_deliveries,
                "deliveries_inserted_total": total_inserted,
                "elapsed_seconds": round(time.time() - start_time, 3),
                "updated_at_unix": time.time(),
            }
        )
        raise
    finally:
        con.close()
        
    duration = time.time() - start_time
    logger.info(f"[+] INGESTION COMPLETE")
    logger.info(f"    Processed: {len(new_files)} matches")
    logger.info(f"    Total Rows: {total_deliveries:,}")
    logger.info(f"    Time: {duration/60:.2f} minutes")

    _write_status(
        {
            "status": "complete",
            "raw_dir": str(RAW_DIR),
            "db_path": str(db_path),
            "total_matches_target": len(new_files),
            "matches_processed": matches_ingested,
            "deliveries_parsed_total": total_deliveries,
            "deliveries_inserted_total": total_inserted,
            "elapsed_seconds": round(duration, 3),
            "updated_at_unix": time.time(),
        }
    )
    
    # Phase 5: Optimize after significant load
    if total_deliveries > 0:
        optimize_database(db_path)
        
    return len(new_files)

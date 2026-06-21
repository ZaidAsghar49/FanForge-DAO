import os
import sys
import sqlite3
import re
import asyncio
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# ── Root path setup so all engine imports resolve correctly ───────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = FastAPI(
    title="FanForge Truth-O-Meter API",
    description=(
        "Full-stack cricket fact-checking API. "
        "Validates natural-language claims and uploaded documents "
        "against the clean international cricket dataset (ODI/Test, 2019-2026)."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = Path("Dataset/Processed/cricket_clean_38.db")
GZ_DB_PATH = DB_PATH.with_suffix(".db.gz")

# Crucial: Configure the exact path for IdentityEngine before validate_model loads
os.environ["CRICKET_DB_PATH"] = str(DB_PATH.absolute())

def check_and_decompress_db():
    if not DB_PATH.exists() and GZ_DB_PATH.exists():
        import gzip
        import shutil
        print(f"[DB] Decompressing database from {GZ_DB_PATH} to {DB_PATH}...")
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = DB_PATH.with_suffix(".tmp")
        try:
            with gzip.open(GZ_DB_PATH, 'rb') as f_in:
                with open(temp_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            temp_path.rename(DB_PATH)
            print("[DB] Decompression complete.")
        except Exception as e:
            if temp_path.exists():
                temp_path.unlink()
            print(f"[DB] ERROR decompressing database: {e}")
            raise e

check_and_decompress_db()

class SQLQuery(BaseModel):
    sql: str

def get_db_connection():
    if not DB_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Database file not found at {DB_PATH}. Please run the extraction script first."
        )
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute("PRAGMA synchronous = NORMAL;")
        cur.execute("PRAGMA cache_size = -131072;")
        cur.execute("PRAGMA temp_store = MEMORY;")
        cur.execute("PRAGMA busy_timeout = 30000;")
        cur.close()
    except Exception as e:
        print(f"[DB-WARN] Failed to configure SQLite PRAGMAs: {e}")
    conn.row_factory = sqlite3.Row
    return conn

# --- API Endpoints ---

@app.get("/api/stats")
def get_stats():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Match count and formats
        cur.execute("SELECT match_format, COUNT(*) as cnt FROM matches GROUP BY match_format")
        format_counts = {row["match_format"]: row["cnt"] for row in cur.fetchall()}
        total_matches = sum(format_counts.values())

        # Date range
        cur.execute("SELECT MIN(date), MAX(date) FROM matches")
        date_row = cur.fetchone()
        min_date = date_row[0] if date_row else "N/A"
        max_date = date_row[1] if date_row else "N/A"

        # Deliveries count
        cur.execute("SELECT COUNT(*) FROM deliveries")
        total_deliveries = cur.fetchone()[0]

        # Unique teams
        cur.execute("SELECT DISTINCT team FROM (SELECT team1 as team FROM matches UNION SELECT team2 as team FROM matches)")
        teams = [row["team"] for row in cur.fetchall() if row["team"]]

        # Player count
        cur.execute("SELECT COUNT(DISTINCT player_name) FROM players")
        total_players = cur.fetchone()[0]

        return {
            "total_matches": total_matches,
            "format_counts": format_counts,
            "date_range": {"min": min_date, "max": max_date},
            "total_deliveries": total_deliveries,
            "total_players": total_players,
            "teams": sorted(teams)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/matches")
def get_matches(
    format: str = Query(None, description="Match format: Test or ODI"),
    year: str = Query(None, description="Year of the match (e.g. 2023)"),
    team: str = Query(None, description="Team name"),
    search: str = Query(None, description="Search venue, city, or player of match"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100)
):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        query_parts = ["SELECT * FROM matches WHERE 1=1"]
        params = {}

        if format:
            query_parts.append("AND match_format = :format")
            params["format"] = format
        if year:
            query_parts.append("AND date LIKE :year_pattern")
            params["year_pattern"] = f"{year}%"
        if team:
            query_parts.append("AND (team1 = :team OR team2 = :team)")
            params["team"] = team
        if search:
            query_parts.append("AND (venue LIKE :search OR city LIKE :search OR player_of_match LIKE :search)")
            params["search"] = f"%{search}%"

        # Count total matches matching criteria
        count_query = query_parts[0].replace("SELECT *", "SELECT COUNT(*)") + " " + " ".join(query_parts[1:])
        cur.execute(count_query, params)
        total_records = cur.fetchone()[0]

        # Fetch records
        query_parts.append("ORDER BY date DESC LIMIT :limit OFFSET :offset")
        params["limit"] = limit
        params["offset"] = (page - 1) * limit

        full_query = " ".join(query_parts)
        cur.execute(full_query, params)
        rows = cur.fetchall()

        matches_list = []
        for row in rows:
            matches_list.append({
                "match_id": row["match_id"],
                "match_format": row["match_format"],
                "season": row["season"],
                "date": row["date"],
                "venue": row["venue"],
                "city": row["city"],
                "team1": row["team1"],
                "team2": row["team2"],
                "toss_winner": row["toss_winner"],
                "toss_decision": row["toss_decision"],
                "result": row["result"],
                "result_winner": row["result_winner"],
                "result_margin": row["result_margin"],
                "result_unit": row["result_unit"],
                "player_of_match": row["player_of_match"],
                "overs_per_inns": row["overs_per_inns"],
                "total_deliveries": row["total_deliveries"]
            })

        return {
            "total_matches": total_records,
            "page": page,
            "limit": limit,
            "total_pages": (total_records + limit - 1) // limit,
            "matches": matches_list
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/match/{match_id}")
def get_match_detail(match_id: str):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Match info
        cur.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,))
        match_row = cur.fetchone()
        if not match_row:
            raise HTTPException(status_code=404, detail="Match not found")

        match_info = dict(match_row)

        # Team squads
        cur.execute("SELECT team, player_name FROM players WHERE match_id = ? ORDER BY team, player_name", (match_id,))
        squad_rows = cur.fetchall()
        squads = {}
        for r in squad_rows:
            squads.setdefault(r["team"], []).append(r["player_name"])

        # Batting performances
        # Note: Order by MIN(over * 100 + ball) orders players by when they came to bat
        cur.execute("""
            SELECT 
                batting_team, 
                innings, 
                batter, 
                SUM(runs_batter) as runs, 
                COUNT(CASE WHEN is_wide = 0 THEN 1 END) as balls, 
                SUM(CASE WHEN runs_batter = 4 THEN 1 ELSE 0 END) as fours, 
                SUM(CASE WHEN runs_batter = 6 THEN 1 ELSE 0 END) as sixes, 
                MAX(wicket_kind) as wicket_kind, 
                MAX(bowler) as dismisser, 
                MAX(fielder) as fielder, 
                MAX(player_out) as player_out
            FROM deliveries 
            WHERE match_id = ? 
            GROUP BY batting_team, innings, batter 
            ORDER BY innings, MIN(over * 100 + ball)
        """, (match_id,))
        batting_rows = cur.fetchall()

        batting_data = {}
        for r in batting_rows:
            inns = r["innings"]
            team = r["batting_team"]
            batting_data.setdefault(inns, {}).setdefault("team", team)
            batting_data[inns].setdefault("batting", []).append({
                "batter": r["batter"],
                "runs": r["runs"],
                "balls": r["balls"],
                "fours": r["fours"],
                "sixes": r["sixes"],
                "wicket_kind": r["wicket_kind"] if r["player_out"] == r["batter"] else None,
                "dismisser": r["dismisser"] if r["player_out"] == r["batter"] else None,
                "fielder": r["fielder"] if r["player_out"] == r["batter"] else None
            })

        # Bowling performances
        cur.execute("""
            SELECT 
                bowling_team, 
                innings, 
                bowler, 
                COUNT(CASE WHEN is_wide = 0 AND is_noball = 0 THEN 1 END) as valid_balls, 
                SUM(runs_batter) + SUM(CASE WHEN is_wide = 1 OR is_noball = 1 THEN runs_extras ELSE 0 END) as runs_conceded, 
                SUM(CASE WHEN wicket_kind IS NOT NULL AND wicket_kind NOT IN ('run out', 'retired hurt', 'obstructing the field') THEN 1 ELSE 0 END) as wickets,
                SUM(CASE WHEN runs_total = 0 AND is_wide = 0 AND is_noball = 0 THEN 1 ELSE 0 END) as dot_balls
            FROM deliveries 
            WHERE match_id = ? 
            GROUP BY bowling_team, innings, bowler 
            ORDER BY innings, MIN(over * 100 + ball)
        """, (match_id,))
        bowling_rows = cur.fetchall()

        for r in bowling_rows:
            inns = r["innings"]
            team = r["bowling_team"]
            
            # Overs calculation e.g. 15 balls = 2.3 overs
            balls = r["valid_balls"]
            overs_str = f"{balls // 6}.{balls % 6}"

            batting_data.setdefault(inns, {}).setdefault("bowling", []).append({
                "bowler": r["bowler"],
                "overs": overs_str,
                "runs_conceded": r["runs_conceded"],
                "wickets": r["wickets"],
                "dot_balls": r["dot_balls"]
            })

        # Team innings summaries (totals and extras)
        cur.execute("""
            SELECT 
                batting_team, 
                innings, 
                SUM(runs_total) as total_runs, 
                SUM(runs_extras) as total_extras,
                COUNT(CASE WHEN wicket_kind IS NOT NULL AND player_out IS NOT NULL THEN 1 END) as total_wickets
            FROM deliveries 
            WHERE match_id = ? 
            GROUP BY batting_team, innings 
            ORDER BY innings
        """, (match_id,))
        summary_rows = cur.fetchall()

        for r in summary_rows:
            inns = r["innings"]
            if inns in batting_data:
                batting_data[inns]["total_runs"] = r["total_runs"]
                batting_data[inns]["total_extras"] = r["total_extras"]
                batting_data[inns]["total_wickets"] = r["total_wickets"]

        # Convert batting_data dictionary to list format sorted by innings
        innings_list = []
        for inns_num in sorted(batting_data.keys()):
            inns_info = batting_data[inns_num]
            inns_info["innings_number"] = inns_num
            innings_list.append(inns_info)

        return {
            "match_info": match_info,
            "squads": squads,
            "innings": innings_list
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/player/search")
def search_players(q: str = Query(..., min_length=2, description="Query to search players")):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT DISTINCT player_name 
            FROM players 
            WHERE player_name LIKE ? 
            LIMIT 15
        """, (f"%{q}%",))
        players = [row["player_name"] for row in cur.fetchall()]
        return {"players": players}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/player/stats")
def get_player_stats(player: str = Query(..., description="Exact player name")):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Check if player exists
        cur.execute("SELECT COUNT(*) FROM players WHERE player_name = ?", (player,))
        if cur.fetchone()[0] == 0:
            raise HTTPException(status_code=404, detail="Player not found in clean dataset")

        # --- Batting stats ---
        # 1. Runs, balls, fours, sixes per format
        cur.execute("""
            SELECT 
                match_format,
                COUNT(DISTINCT match_id) as innings,
                SUM(runs_batter) as runs,
                COUNT(CASE WHEN is_wide = 0 THEN 1 END) as balls,
                SUM(CASE WHEN runs_batter = 4 THEN 1 ELSE 0 END) as fours,
                SUM(CASE WHEN runs_batter = 6 THEN 1 ELSE 0 END) as sixes
            FROM deliveries 
            WHERE batter = ?
            GROUP BY match_format
        """, (player,))
        batting_rows = cur.fetchall()

        # 2. Match runs list to calculate best, centuries, 50s and dismissals
        cur.execute("""
            SELECT 
                match_format,
                match_id,
                SUM(runs_batter) as match_runs,
                SUM(CASE WHEN player_out = batter THEN 1 ELSE 0 END) as is_dismissed
            FROM deliveries 
            WHERE batter = ?
            GROUP BY match_format, match_id
        """, (player,))
        match_batting_rows = cur.fetchall()

        batting_stats = {}
        # Initialise per format
        for row in batting_rows:
            fmt = row["match_format"]
            batting_stats[fmt] = {
                "innings": row["innings"],
                "runs": row["runs"],
                "balls": row["balls"],
                "fours": row["fours"],
                "sixes": row["sixes"],
                "dismissals": 0,
                "not_outs": row["innings"],
                "high_score": 0,
                "fifties": 0,
                "hundreds": 0,
                "average": 0.0,
                "strike_rate": 0.0
            }

        # Calculate high score, 50s, 100s, dismissals
        for row in match_batting_rows:
            fmt = row["match_format"]
            runs = row["match_runs"]
            dismissed = row["is_dismissed"] > 0

            if fmt not in batting_stats:
                continue

            stat = batting_stats[fmt]
            if runs > stat["high_score"]:
                stat["high_score"] = runs
                # Add indicator if not out (not dismissed)
                stat["high_score_not_out"] = not dismissed

            if dismissed:
                stat["dismissals"] += 1
                stat["not_outs"] = max(0, stat["innings"] - stat["dismissals"])

            if runs >= 100:
                stat["hundreds"] += 1
            elif runs >= 50:
                stat["fifties"] += 1

        # Post-process batting avgs & strike rates
        for fmt, stat in batting_stats.items():
            div = stat["dismissals"] if stat["dismissals"] > 0 else 1
            stat["average"] = round(stat["runs"] / div, 2) if stat["dismissals"] > 0 else stat["runs"]
            stat["strike_rate"] = round((stat["runs"] / stat["balls"]) * 100, 2) if stat["balls"] > 0 else 0.0

        # --- Bowling stats ---
        # 1. Total overs, runs, wickets per format
        cur.execute("""
            SELECT 
                match_format,
                COUNT(DISTINCT match_id) as matches,
                COUNT(CASE WHEN is_wide = 0 AND is_noball = 0 THEN 1 END) as valid_balls,
                SUM(runs_batter) + SUM(CASE WHEN is_wide = 1 OR is_noball = 1 THEN runs_extras ELSE 0 END) as runs_conceded,
                SUM(CASE WHEN wicket_kind IS NOT NULL AND wicket_kind NOT IN ('run out', 'retired hurt', 'obstructing the field') THEN 1 ELSE 0 END) as wickets
            FROM deliveries
            WHERE bowler = ?
            GROUP BY match_format
        """, (player,))
        bowling_rows = cur.fetchall()

        # 2. Bowling match breakdown to get best spell & 5w hauls
        cur.execute("""
            SELECT 
                match_format,
                match_id,
                SUM(CASE WHEN wicket_kind IS NOT NULL AND wicket_kind NOT IN ('run out', 'retired hurt', 'obstructing the field') THEN 1 ELSE 0 END) as wickets,
                SUM(runs_batter) + SUM(CASE WHEN is_wide = 1 OR is_noball = 1 THEN runs_extras ELSE 0 END) as runs_conceded
            FROM deliveries
            WHERE bowler = ?
            GROUP BY match_format, match_id
        """, (player,))
        match_bowling_rows = cur.fetchall()

        bowling_stats = {}
        for row in bowling_rows:
            fmt = row["match_format"]
            balls = row["valid_balls"]
            overs_str = f"{balls // 6}.{balls % 6}"
            
            bowling_stats[fmt] = {
                "matches": row["matches"],
                "overs": overs_str,
                "runs_conceded": row["runs_conceded"],
                "wickets": row["wickets"],
                "five_wickets": 0,
                "best_bowling": "N/A",
                "best_wickets": -1,
                "best_runs": 9999,
                "average": 0.0,
                "economy": 0.0
            }

        for row in match_bowling_rows:
            fmt = row["match_format"]
            w = row["wickets"]
            r = row["runs_conceded"]

            if fmt not in bowling_stats:
                continue

            stat = bowling_stats[fmt]
            if w >= 5:
                stat["five_wickets"] += 1
            
            # Best spell evaluation (wickets primary desc, runs secondary asc)
            if w > stat["best_wickets"] or (w == stat["best_wickets"] and r < stat["best_runs"]):
                stat["best_wickets"] = w
                stat["best_runs"] = r
                stat["best_bowling"] = f"{w}/{r}"

        # Post-process bowling averages & economy
        for fmt, stat in bowling_stats.items():
            # Convert overs str back to fraction of overs for economy
            overs_parts = stat["overs"].split(".")
            overs_dec = float(overs_parts[0]) + (float(overs_parts[1]) / 6.0) if len(overs_parts) > 1 else float(overs_parts[0])
            
            stat["average"] = round(stat["runs_conceded"] / stat["wickets"], 2) if stat["wickets"] > 0 else 0.0
            stat["economy"] = round(stat["runs_conceded"] / overs_dec, 2) if overs_dec > 0 else 0.0
            
            # Clean temporary fields
            del stat["best_wickets"]
            del stat["best_runs"]

        # Historical match scores for charting (runs per match)
        cur.execute("""
            SELECT 
                d.match_format,
                d.date,
                m.runs
            FROM (
                SELECT match_id, SUM(runs_batter) as runs 
                FROM deliveries 
                WHERE batter = ? 
                GROUP BY match_id
            ) m
            JOIN matches d ON m.match_id = d.match_id
            ORDER BY d.date ASC
        """, (player,))
        runs_history = [{"format": r["match_format"], "date": r["date"], "runs": r["runs"]} for r in cur.fetchall()]

        return {
            "player": player,
            "batting": batting_stats,
            "bowling": bowling_stats,
            "runs_history": runs_history
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/api/query")
def execute_query(query: SQLQuery):
    sql_trimmed = query.sql.strip()
    
    # Validation check: ONLY SELECT is allowed
    if not re.match(r"^SELECT\b", sql_trimmed, re.IGNORECASE):
        raise HTTPException(
            status_code=400,
            detail="Forbidden. Only SELECT queries are permitted for data safety."
        )

    # Secondary check for query modification keywords
    blacklisted = ["insert", "update", "delete", "drop", "alter", "create", "replace", "vacuum", "pragma"]
    for keyword in blacklisted:
        if re.search(r"\b" + keyword + r"\b", sql_trimmed, re.IGNORECASE):
            raise HTTPException(
                status_code=400,
                detail=f"Forbidden. Query contains blacklisted command keyword: '{keyword}'."
            )

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql_trimmed)
        rows = cur.fetchall()
        
        # Get column names
        columns = [desc[0] for desc in cur.description] if cur.description else []
        
        # Format rows
        data = [dict(row) for row in rows]
        
        return {
            "columns": columns,
            "rows": data,
            "row_count": len(data)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"SQL Error: {str(e)}")
    finally:
        conn.close()

# =============================================================================
# Truth-O-Meter Verification Endpoints
# =============================================================================

class ClaimRequest(BaseModel):
    claim: str
    skip_predictions: Optional[bool] = False


@app.post("/api/v1/verify/claim")
async def verify_claim_text(body: ClaimRequest):
    """
    Verify a single natural-language cricket claim.
    Routes the claim through the full 4-phase Truth-O-Meter pipeline:
      Phase 1: Semantic Parsing (LLM → structured JSON)
      Phase 2: Identity Resolution (10-tier cascading engine)
      Phase 3: Query Planning (38-parameter filter build)
      Phase 4: Data Execution + Verdict
    """
    claim = body.claim.strip()
    if not claim:
        raise HTTPException(status_code=400, detail="Claim text cannot be empty.")
    if len(claim) > 2000:
        raise HTTPException(status_code=400, detail="Claim must be ≤ 2000 characters.")

    try:
        loop = asyncio.get_event_loop()
        from clean_analysis.validate_model import validate_claim
        result = await loop.run_in_executor(
            None,
            lambda: validate_claim(claim, skip_predictions=body.skip_predictions)
        )
        # Inject original claim string for frontend display
        result["claim"] = claim
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Verification pipeline error: {e}")


@app.post("/api/v1/verify/file")
async def verify_claim_file(
    file: UploadFile = File(..., description="Upload a .txt or .pdf file containing cricket claims"),
    max_claims: int = Query(default=15, ge=1, le=30, description="Max claims to verify"),
    skip_predictions: bool = Query(default=True, description="Skip ML prediction stage for speed"),
):
    """
    Intelligent Document Processing (IDP) endpoint.
    Accepts .txt or .pdf files, extracts cricket statistical assertions,
    maps them through the IdentityEngine, and returns an array of
    Truth-O-Meter verdicts — one per isolated claim.

    Pipeline:
      Stage 1 : Multi-Format Text Extraction (streaming)
      Stage 2 : Semantic Chunking & Claim Isolation
      Stage 3 : Cascading Identity Resolution
      Stage 4 : Truth-O-Meter Verdict Dispatch
    """
    # Validate file type
    if file.filename is None:
        raise HTTPException(status_code=400, detail="No file provided.")

    ext = Path(file.filename).suffix.lower()
    if ext not in (".txt", ".pdf"):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{ext}'. Only .txt and .pdf are accepted."
        )

    # Read file bytes (memory-safe; 250 MB guard)
    MAX_SIZE_BYTES = 250 * 1024 * 1024  # 250 MB
    try:
        file_bytes = await file.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File read error: {e}")

    if len(file_bytes) > MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(file_bytes) / 1_048_576:.1f} MB). Max allowed: 250 MB."
        )
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Run IDP pipeline in executor (non-blocking)
    try:
        from file_claim_parser import parse_document_claims
        loop = asyncio.get_event_loop()
        verdicts = await loop.run_in_executor(
            None,
            lambda: parse_document_claims(
                file_bytes,
                file.filename,
                skip_predictions=skip_predictions,
                max_claims=max_claims,
            )
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"IDP pipeline error: {e}")

    return {
        "filename": file.filename,
        "file_size_kb": round(len(file_bytes) / 1024, 1),
        "claims_found": len(verdicts),
        "verdicts": verdicts,
    }


# =============================================================================
# Serve Static Frontend
# =============================================================================

STATIC_DIR = Path("web_clean/static")
STATIC_DIR.mkdir(parents=True, exist_ok=True)

@app.get("/")
def read_root():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "message": "Backend server is active. Place index.html inside 'web_clean/static' to load the dashboard frontend UI."
    }

app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")

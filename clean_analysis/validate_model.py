"""
validate_model.py — 38-Parameter Filtering & Calculation Engine
================================================================
Validates natural-language cricket claims against ball-by-ball data.

Architecture
────────────
Phase 1 : NL → structured JSON   (ai_parser.parse_claim)
Phase 2 : Player identity resolution (identity_engine.IdentityEngine)
Phase 3 : Data loading & 38-parameter filtering
Phase 4 : Dynamic metric calculation
Phase 5 : Truth-O-Meter verdict

Supported metrics (dynamically calculated):
  Batting  : Batting Average, Strike Rate, Total Runs, Dot Ball %,
             Boundary %, High Score, Milestones (50s / 100s),
             Partnership Runs, Balls Faced, Batting Position avg
  Bowling  : Wickets, Economy Rate, Bowling Strike Rate, Bowling Average,
             Dots Forced, Extras Conceded, Runs Conceded in Over
"""

import json
import os
import re
import sys
from pathlib import Path

# ── Windows UTF-8 stdout fix (prevents charmap crashes from emoji in insights) ─
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import pandas as pd

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.analysis.ai_parser import parse_claim
from scripts.identity.identity_engine import IdentityEngine
from scripts.pipeline.city_map import CITY_COUNTRY_MAP
# Prediction engine imported lazily: it loads sklearn/XGBoost models which consume significant RAM
from scripts.analysis.insight_generator import generate_insight
from scripts.analysis.feature_registry import (
    FEATURE_REGISTRY, EXECUTION_MODE, FeatureMissingError,
    COMPETITION_MAP, FORMAT_MATCH_TYPE_MAP,
    validate_features, resolve_competition, resolve_format,
)
from scripts.analysis.query_planner import (
    QueryPlanner, ExecutionPlan, FilterSet, is_comparative,
    _ASIA_COUNTRIES,
)
from scripts.analysis.metric_registry import compute_metric
def _truncate_as_of(df: pd.DataFrame, as_of_date: str | None) -> pd.DataFrame:
    """Truncate dataset to rows with date <= as_of_date (YYYY-MM-DD)."""
    if not as_of_date or df.empty:
        return df
    if "date" not in df.columns:
        return df
    try:
        cutoff = pd.to_datetime(as_of_date, errors="raise")
        dates = pd.to_datetime(df["date"], errors="coerce")
        return df[dates.notna() & (dates <= cutoff)]
    except Exception:
        return df

def _lazy_prediction(df, canonical, is_batting, active_filters):
    """Lazy-load and run prediction engine only when called."""
    try:
        from scripts.analysis.prediction.prediction_engine import (
            run_prediction_pipeline, format_prediction_output
        )
        preds = run_prediction_pipeline(df, canonical, is_batting, active_filters)
        out = format_prediction_output(preds, is_batting)
        try:
            print(out)
        except UnicodeEncodeError:
            # Windows consoles can choke on some unicode; degrade safely.
            print(out.encode("utf-8", "backslashreplace").decode("utf-8"))
        return preds
    except Exception as e:
        preds = {"error": f"Prediction unavailable: {e}"}
        try:
            print(f"\n[Predictive Analysis]\n  Prediction skipped: {e}\n")
        except UnicodeEncodeError:
            msg = str(e).encode("utf-8", "backslashreplace").decode("utf-8")
            print(f"\n[Predictive Analysis]\n  Prediction skipped: {msg}\n")
        return preds


# ── Data sources ────────────────────────────────────────────────
# Prefer Parquet (faster, smaller) — fall back to CSV automatically
MATCHES_PARQUET = str(ROOT / "matches.parquet")
MATCHES_CSV     = str(ROOT / "matches.csv")
BOWLERS_FILE    = str(ROOT / "bowlers.csv")
DUCKDB_PATH     = str(ROOT / "data" / "processed" / "cricket.duckdb")
SQLITE_DB       = str(ROOT / "Dataset" / "Processed" / "cricket_clean_38.db")

def _ensure_database_decompressed():
    from pathlib import Path
    import gzip
    import shutil
    db_p = Path(SQLITE_DB)
    gz_p = db_p.with_suffix(".db.gz")
    if not db_p.exists() and gz_p.exists():
        print(f"    [DB] Decompressing clean database from {gz_p}...")
        db_p.parent.mkdir(parents=True, exist_ok=True)
        temp_p = db_p.with_suffix(".tmp")
        try:
            with gzip.open(gz_p, 'rb') as f_in:
                with open(temp_p, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            temp_p.rename(db_p)
            print("    [DB] Database decompression complete.")
        except Exception as e:
            if temp_p.exists():
                temp_p.unlink()
            print(f"    [DB] ERROR decompressing database: {e}")
            raise e

_ensure_database_decompressed()

# ── New-schema columns that may already be pre-computed in the 38-col CSV/Parquet ──
# When these columns exist we use them directly (no IdentityEngine lookup per row).
_NEW_SCHEMA_COLS = {
    "bowler_type", "bowler_hand", "match_phase", "innings", "over",
    "venue_name", "country", "season", "competition", "day_night",
    "neutral_venue", "toss_winner", "toss_decision",
    "home_team", "non_striker", "batting_position", "wicket_type",
    "extras_wides", "extras_noballs", "extras_byes", "extras_legbyes",
    "batter_hand",
}


# ── Global Metric Contexts ────────────────────────────────────────────────────
# Used to determine if we should look at 'batter' or 'bowler' column in the DB.
_BOWLING_SPECIFIC = {
    "wickets", "economy rate", "economy", "dots forced", "extras conceded",
    "extras", "runs conceded in over", "runs conceded", "bowling average",
    "bowling strike rate"
}
_BATTING_SPECIFIC = {
    "high score", "milestones", "partnership runs", "balls faced",
    "batting position avg", "boundary %", "dot ball %", "batting average",
    "batting strike rate"
}
_AMBIGUOUS_METRICS = {"average", "strike rate", "runs", "total runs"}

# ── Global Cache / Singleton State ───────────────────────────────────────────
_ENGINE_INSTANCE: IdentityEngine | None = None
_PLAYER_ALIAS_CACHE: dict[tuple[str, str], list[str]] = {}  # (canonical, col) → [aliases]
_BOWLER_STYLE_CACHE: dict[str, tuple[str, str]] = {}  # name → (type, hand)
_SCORECARD_CACHE_FILE = ROOT / "data" / "scorecard_aliases_cache.json"
_SCORECARD_CACHE: dict[str, list[str]] = {}  # (col:canonical) -> [aliases]

def _get_engine() -> IdentityEngine:
    global _ENGINE_INSTANCE
    if _ENGINE_INSTANCE is None:
        _ENGINE_INSTANCE = IdentityEngine(SQLITE_DB)
    return _ENGINE_INSTANCE


def _load_bowler_db() -> None:
    """Populate the global bowler-style cache from bowlers.csv."""
    global _BOWLER_STYLE_CACHE
    if _BOWLER_STYLE_CACHE:
        return
    try:
        bdf = pd.read_csv(BOWLERS_FILE)
        for _, row in bdf.iterrows():
            style = str(row.get("style", "")).strip()
            btype = "Spin" if style == "Spin" else "Pace" if style == "Pace" else "Unknown"
            # Hand inference is heuristic from name conventions; IdentityEngine
            # provides the authoritative answer via batting/bowling style field.
            _BOWLER_STYLE_CACHE[str(row["bowler"]).strip()] = (btype, "Unknown")
    except Exception as exc:
        print(f"    [WARN] Could not load bowlers.csv: {exc}")


# ── Utility helpers ───────────────────────────────────────────────────────────

def _city_to_country(city: str) -> str:
    return CITY_COUNTRY_MAP.get(city, "Unknown")


def _bowler_style(engine: IdentityEngine, bowler_name: str) -> tuple[str, str]:
    """Return (bowler_type, bowler_hand) for a bowler name.

    Resolution order:
      1. bowlers.csv cache (fastest)
      2. IdentityEngine → players_db bowling-style field
    """
    if bowler_name in _BOWLER_STYLE_CACHE:
        return _BOWLER_STYLE_CACHE[bowler_name]

    res = engine.resolve_for_ingestion(bowler_name)
    if res:
        raw = res.get("bowling_type", "Unknown")   # "Spin" / "Pace" / "Unknown"
        # Determine hand from the players_db bowlingstyle string if available
        rows = engine.players_db[engine.players_db["fullname"] == res.get("canonical_name", "")]
        hand = "Unknown"
        if not rows.empty:
            style_str = str(rows.iloc[0].get("bowlingstyle", "")).lower()
            if "left" in style_str:
                hand = "Left"
            elif "right" in style_str:
                hand = "Right"
        _BOWLER_STYLE_CACHE[bowler_name] = (raw, hand)
        return (raw, hand)

    return ("Unknown", "Unknown")


def _get_match_phase(over: int) -> str:
    """Legacy fallback classifier (0-indexed). Prefer _phase_from_context."""
    if over <= 5:
        return "Powerplay"
    if over <= 14:
        return "Middle"
    return "Death"


def _phase_from_context(over_0: int, match_type: str | None, overs_limit: int | None) -> str:
    """
    Cricket rules (limited-overs):
    - T20/T20I powerplay: overs 1–6  (0–5)
    - ODI powerplay:      overs 1–10 (0–9)
    - Death overs: last 5 overs of the innings (overs_limit-5 .. overs_limit-1)
    """
    mt = (match_type or "").lower()
    ol = int(overs_limit) if overs_limit is not None and str(overs_limit).isdigit() else None
    if ol is None:
        # Infer typical overs limit from match type where possible
        if "odi" in mt or mt in {"odm"}:
            ol = 50
        elif "t20" in mt:
            ol = 20

    if ol is not None and over_0 >= max(0, ol - 5):
        return "Death"

    # Powerplay
    if "odi" in mt:
        return "Powerplay" if over_0 <= 9 else "Middle"
    if "t20" in mt:
        return "Powerplay" if over_0 <= 5 else "Middle"

    # Unknown / Tests: fall back to coarse bins
    return _get_match_phase(over_0)


def _resolve_player(engine: IdentityEngine, name: str) -> dict | None:
    """Wrap IdentityEngine.resolve_for_ingestion with a clear error message."""
    if not name:
        return None
    result = engine.resolve_for_ingestion(name)
    if not result:
        print(f"    [FAIL] Identity resolution FAILED for '{name}'.")
    return result

def _get_required_columns(metric: str, filters: dict) -> list[str]:
    """Determine minimum columns needed for the query to save I/O."""
    metric_l = metric.lower()
    cols = {"match_id", "innings", "over", "ball", "batter", "bowler"} # Base
    
    # Add columns based on filters
    for key, val in filters.items():
        if val is not None:
            if key == "venue_name": cols.add("venue_name")
            elif key == "city": cols.add("city")
            elif key == "country": cols.add("city"); cols.add("country")
            elif key == "format": 
                cols.update(["match_type", "competition", "batting_team", "bowling_team"])
            elif key == "season": cols.add("date")
            elif key == "as_of_date": cols.add("date")
            elif key == "day_night": cols.add("day_night")
            elif key == "toss_winner": cols.update(["toss_winner", "batting_team"])
            elif key == "toss_decision": cols.add("toss_decision")
            elif key == "series": cols.add("competition")
            elif key == "home_away": cols.update(["home_team", "batting_team", "bowling_team"])
            elif key == "neutral_venue": cols.add("neutral_venue")
            elif key == "dismissal_type": cols.add("wicket_type")
            elif key == "batting_position": cols.add("batting_position")
            elif key == "non_striker": cols.add("non_striker")
            elif key == "opposition": cols.update(["bowling_team", "batting_team"])
            elif key == "bowler_type": cols.add("bowler_type")
            elif key == "bowler_hand": cols.add("bowler_hand")
            elif key == "match_phase":
                # If match_phase isn't precomputed, we may need match metadata to classify it.
                cols.update(["match_phase", "over", "match_type", "overs_limit"])
            elif key == "batter_vs_bowler_type": cols.add("bowler_type")
            elif key == "_is_comparison_half": cols.add("country")

    # Add columns based on metric
    if any(m in metric_l for m in ["average", "strike rate", "runs", "wicket", "economy", "dots"]):
        cols.update(["runs_batter", "is_wicket", "extras_wides", "runs_total", "is_bowler_wicket", "extras_noballs"])
    if "dots" in metric_l:
        cols.add("runs_total")
    if "boundary" in metric_l:
        cols.add("runs_batter")
    if "high score" in metric_l or "milestone" in metric_l:
        cols.update(["match_id", "innings", "runs_batter"])
    if "extras" in metric_l:
        cols.update(["extras_wides", "extras_noballs", "extras_byes", "extras_legbyes"])

    # Ensure all exist in delivery schema (intersect with known columns if possible)
    return sorted(list(cols))


def _get_scorecard_aliases(col: str, canonical_name: str, engine: IdentityEngine,
                           db_path: str) -> list[str]:
    """
    Cached scorecard string lookup.
    """
    cache_key = f"{col}:{canonical_name}"
    
    # 1. Memory Cache
    if cache_key in _PLAYER_ALIAS_CACHE:
        return _PLAYER_ALIAS_CACHE[cache_key]

    # 2. Persistent Disk Cache
    global _SCORECARD_CACHE
    if not _SCORECARD_CACHE and _SCORECARD_CACHE_FILE.exists():
        try:
            with open(_SCORECARD_CACHE_FILE, "r") as f:
                _SCORECARD_CACHE = json.load(f)
        except Exception:
            pass
            
    if cache_key in _SCORECARD_CACHE:
        _PLAYER_ALIAS_CACHE[cache_key] = _SCORECARD_CACHE[cache_key]
        return _SCORECARD_CACHE[cache_key]

    import duckdb as _duckdb
    last_name = canonical_name.split()[-1]
    
    # Use DuckDB for high-speed alias lookup
    if os.path.exists(DUCKDB_PATH):
        try:
            con = _duckdb.connect(DUCKDB_PATH, read_only=True)
            # Use DuckDB's fast string matching
            query = f"SELECT DISTINCT {col} FROM deliveries WHERE {col} ILIKE ?"
            res = con.execute(query, (f"%{last_name}%",)).fetchall()
            candidates = [r[0] for r in res]
            con.close()
        except Exception as exc:
            print(f"    [WARN] DuckDB alias scan failed: {exc}")
            candidates = []
    elif os.path.exists(db_path):
        import sqlite3 as _sqlite3
        try:
            con = _sqlite3.connect(db_path)
            cur = con.cursor()
            cur.execute(
                f"SELECT DISTINCT {col} FROM deliveries WHERE {col} LIKE ?",
                (f"%{last_name}%",)
            )
            candidates = [r[0] for r in cur.fetchall()]
            con.close()
        except Exception as exc:
            print(f"    [WARN] SQLite alias scan failed for '{canonical_name}' on col '{col}': {exc}")
            return []
    else:
        return []

    # Use the player's own country as a disambiguation hint
    subj_meta = engine.resolve_for_ingestion(canonical_name)
    team_hint = subj_meta.get("country") if subj_meta else None
    canonical_last = canonical_name.split()[-1].lower()

    from rapidfuzz import fuzz
    # Count how many candidates share the same last name to detect common surnames
    same_last_candidates = [c for c in candidates if c.strip() and c.split()[-1].lower() == canonical_last]
    is_common_surname = len(same_last_candidates) >= 2  # Anderson, Kumar, Khan, etc.

    aliases: list[str] = []
    for raw in candidates:
        raw_last = raw.split()[-1].lower() if raw.strip() else ""
        # Must share the same last name
        if raw_last != canonical_last:
            continue

        fuzz_score = fuzz.token_set_ratio(canonical_name.lower(), raw.lower())

        if is_common_surname:
            # For common surnames, ALWAYS verify through identity engine
            res = engine.resolve_for_ingestion(raw, team=team_hint)
            if res and res["canonical_name"] == canonical_name:
                aliases.append(raw)
            elif fuzz_score >= 95:
                # Very high fuzzy = exact match like "James Anderson" ↔ "James Anderson"
                aliases.append(raw)
        else:
            # Unique surname: fuzzy match is sufficient
            if fuzz_score >= 85:
                aliases.append(raw)
            else:
                res = engine.resolve_for_ingestion(raw, team=team_hint)
                if res and res["canonical_name"] == canonical_name:
                    aliases.append(raw)

    if canonical_name not in aliases:
        aliases.append(canonical_name)

    res_list = list(set(aliases))
    _PLAYER_ALIAS_CACHE[cache_key] = res_list
    
    # Update persistent cache
    _SCORECARD_CACHE[cache_key] = res_list
    try:
        _SCORECARD_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_SCORECARD_CACHE_FILE, "w") as f:
            json.dump(_SCORECARD_CACHE, f, indent=2)
    except Exception:
        pass

    return res_list

# ── Validation Utilities (Fix 1, Fix 8) ──────────────────────────────────────

def _assert_filter_effective(df_before: pd.DataFrame, df_after: pd.DataFrame,
                             filter_name: str, mode: str = EXECUTION_MODE) -> None:
    """Warn in STRICT mode if a filter that should reduce the dataset did nothing."""
    if mode != "STRICT":
        return
    if len(df_before) > 0 and len(df_after) == len(df_before):
        print(f"    [FILTER-WARN] '{filter_name}' did not reduce dataset "
              f"({len(df_before):,} → {len(df_after):,} rows). Verify column/value exists.")

def _require_column(df: pd.DataFrame, col: str, filter_name: str, mode: str = EXECUTION_MODE) -> None:
    """Raise FeatureMissingError in STRICT mode if col absent."""
    if col not in df.columns:
        if mode == "STRICT":
            raise FeatureMissingError(col, f"Required for filter: {filter_name}")
        else:
            print(f"    [WARN] Column '{col}' missing — filter '{filter_name}' skipped.")

def _validate_dataset_integrity(df: pd.DataFrame, canonical: str) -> dict:
    """Run fast integrity checks on the loaded subject DataFrame."""
    warnings = {}
    if {"match_id", "over", "ball"}.issubset(df.columns):
        # We need \"innings\" too to uniquely identify a delivery
        dup_count = df.duplicated(subset=["match_id", "innings", "over", "ball"]).sum()
        if dup_count > 0:
            warnings["duplicate_deliveries"] = (
                f"{dup_count} duplicate delivery rows detected for '{canonical}'. "
                f"Statistics may be inflated. Run deduplication in ingestion pipeline."
            )
    return warnings


# ── 38-Parameter Filter Application ──────────────────────────────────────────

def apply_filters(df: pd.DataFrame, filters: dict | None = None, engine: IdentityEngine | None = None,
                  is_batting: bool = True, **kwargs) -> pd.DataFrame:
    """
    Apply all 38 supported filter dimensions to the delivery DataFrame.

    Parameters
    ----------
    df         : Full ball-by-ball DataFrame (already pre-filtered by subject).
    filters    : dict produced by ai_parser.parse_claim (optional).
    engine     : IdentityEngine instance (for player resolution).
    is_batting : True if the subject is a batter; False if a bowler.
    **kwargs   : Additional filters passed directly as keywords.
    """
    if filters is None:
        filters = kwargs
    else:
        filters = {**filters, **kwargs}
    
    if engine is None:
        engine = _get_engine()
    
    # Ignore None filters
    filters = {k: v for k, v in filters.items() if v is not None}

    # ── Handedness (explicit parameters) ──────────────────────────────────────
    # Allow direct filtering by batter_hand / bowler_hand for both batting and bowling queries.
    batter_hand = filters.get("batter_hand")
    if batter_hand:
        _require_column(df, "batter_hand", "batter_hand")
        df_before = df
        bh = str(batter_hand).strip().lower()
        bh = "left" if bh.startswith("l") else ("right" if bh.startswith("r") else bh)
        df = df[df["batter_hand"].astype(str).str.lower() == bh]
        _assert_filter_effective(df_before, df, "batter_hand")

    bowler_hand_param = filters.get("bowler_hand")
    if bowler_hand_param and not is_batting:
        _require_column(df, "bowler_hand", "bowler_hand")
        df_before = df
        oh = str(bowler_hand_param).strip().lower()
        oh = "left" if oh.startswith("l") else ("right" if oh.startswith("r") else oh)
        df = df[df["bowler_hand"].astype(str).str.lower() == oh]
        _assert_filter_effective(df_before, df, "bowler_hand")
    # ── 0. Batter (Subject) ────────────────────────────────────────────────────
    batter = filters.get("batter")
    if batter and "batter" in df.columns:
        df_before = df
        df = df[df["batter"].str.lower().str.contains(batter.lower(), na=False)]
        _assert_filter_effective(df_before, df, "batter")

    # ── 1. Venue Name ─────────────────────────────────────────────────────────
    venue = filters.get("venue_name")
    if venue and "venue_name" in df.columns:
        df_before = df
        df = df[df["venue_name"].str.lower().str.contains(venue.lower(), na=False)]
        _assert_filter_effective(df_before, df, "venue_name")

    # ── 2. City ───────────────────────────────────────────────────────────────
    city = filters.get("city")
    if city and "city" in df.columns:
        df_before = df
        df = df[df["city"].str.lower() == city.lower()]
        _assert_filter_effective(df_before, df, "city")

    # ── 3. Country ────────────────────────────────────────────────────────────
    country = filters.get("country")
    if country:
        if "country" in df.columns:
            df_before = df
            df = df[df["country"].str.lower() == country.lower()]
            _assert_filter_effective(df_before, df, "country")
        elif "city" in df.columns:
            df_before = df
            df = df[df["city"].map(CITY_COUNTRY_MAP).fillna("Unknown").str.lower() == country.lower()]
            _assert_filter_effective(df_before, df, "city")

    # ── 4. Format ─────────────────────────────────────────────────────────────
    fmt = filters.get("format")
    if fmt and "match_type" in df.columns:
        # Prefer strict canonical mappings from feature_registry.py
        key = str(fmt).strip().lower()
        comp_vals = resolve_competition(key)
        mt_vals = resolve_format(key)

        if comp_vals and "competition" in df.columns:
            df_before = df
            df = df[df["competition"].isin(comp_vals)]
            _assert_filter_effective(df_before, df, "competition")
        elif mt_vals and "match_type" in df.columns:
            df_before = df
            df = df[df["match_type"].isin(mt_vals)]
            _assert_filter_effective(df_before, df, "match_type")
        else:
            # Unknown format key: in STRICT mode, do not silently "contains" match.
            if EXECUTION_MODE == "STRICT":
                raise ValueError(
                    f"[STRICT MODE] Unrecognised format '{fmt}'. "
                    f"Add it to FORMAT_MATCH_TYPE_MAP or COMPETITION_MAP."
                )
            df_before = df
            df = df[df["match_type"].str.contains(str(fmt), case=False, na=False)]
            _assert_filter_effective(df_before, df, "match_type")

    # ── 5. Season / Year ──────────────────────────────────────────────────────
    season = filters.get("season")
    if season and "date" in df.columns:
        import re as _re
        season_str = str(season).lower()
        range_match = _re.match(r"(\d{4})\s*[-\u2013]\s*(\d{4})", season_str)
        if range_match:
            yr_start = int(range_match.group(1))
            yr_end   = int(range_match.group(2))
            valid_years = [str(y) for y in range(yr_start, yr_end + 1)]
            df_before = df
            df = df[df["date"].astype(str).str[:4].isin(valid_years)]
            _assert_filter_effective(df_before, df, "date")
        elif "after" in season_str or "onwards" in season_str or "since" in season_str:
            match = _re.search(r"(\d{4})", season_str)
            if match:
                yr_start = int(match.group(1))
                df_before = df
                df = df[df["date"].astype(str).str[:4].astype(int) >= yr_start]
                _assert_filter_effective(df_before, df, "date")
        elif "before" in season_str or "until" in season_str:
            match = _re.search(r"(\d{4})", season_str)
            if match:
                yr_end = int(match.group(1))
                df_before = df
                df = df[df["date"].astype(str).str[:4].astype(int) <= yr_end]
                _assert_filter_effective(df_before, df, "date")
        else:
            match = _re.search(r"(\d{4})", season_str)
            if match:
                year = match.group(1)
                df_before = df
                df = df[df["date"].astype(str).str.startswith(year)]
                _assert_filter_effective(df_before, df, "date")

    # ── 6. Day/Night ──────────────────────────────────────────────────────────
    day_night = filters.get("day_night")
    if day_night and "day_night" in df.columns:
        # Normalize user input and DB values to avoid "filter skipped" drift.
        dn_lower = str(day_night).strip().lower().replace("_", "-").replace(" ", "-")
        # Common aliases
        dn_lower = "day-night" if dn_lower in {"day-night", "daynight", "day-nighter", "day-night-match"} else dn_lower
        dn_lower = "day" if dn_lower in {"day", "day-only"} else dn_lower
        available = set(df["day_night"].dropna().str.lower().unique())
        if dn_lower in available:
            df_before = df
            df = df[df["day_night"].str.lower() == dn_lower]
            _assert_filter_effective(df_before, df, "day_night")
        else:
            # IMPORTANT: do not silently skip filters in STRICT mode, it causes stat drift vs reference sites.
            if EXECUTION_MODE == "STRICT":
                raise ValueError(
                    f"[STRICT MODE] day_night='{day_night}' not found in data (available: {sorted(available)}). "
                    f"Either re-ingest with normalized day_night, or remove/relax the filter."
                )
            print(f"    [WARN] day_night='{day_night}' not found in data (have: {available}). Filter skipped.")

    # ── 7. Toss Winner ────────────────────────────────────────────────────────
    toss_winner = filters.get("toss_winner")
    if toss_winner and "toss_winner" in df.columns and "batting_team" in df.columns:
        sentinel = str(toss_winner).lower()
        if sentinel in ("subject_team", "batting_team", "own_team", "their_team"):
            df_before = df
            df = df[df["toss_winner"].str.lower() == df["batting_team"].str.lower()]
            _assert_filter_effective(df_before, df, "toss_winner")
        elif sentinel in ("opponent", "opposition_team", "other_team", "bowling_team"):
            df_before = df
            df = df[df["toss_winner"].str.lower() != df["batting_team"].str.lower()]
            _assert_filter_effective(df_before, df, "toss_winner")
        else:
            df_before = df
            df = df[df["toss_winner"].str.lower() == sentinel]
            _assert_filter_effective(df_before, df, "toss_winner")

    # ── 8. Toss Decision ─────────────────────────────────────────────────────
    toss_decision = filters.get("toss_decision")
    if toss_decision and "toss_decision" in df.columns:
        td = str(toss_decision).lower().strip()
        if td in ("bat", "batting", "bat first"):
            df_before = df
            df = df[df["toss_decision"].str.lower() == "bat"]
            _assert_filter_effective(df_before, df, "toss_decision")
        elif td in ("field", "bowl", "fielding", "bowling", "bowl first", "field first"):
            df_before = df
            df = df[df["toss_decision"].str.lower() == "field"]
            _assert_filter_effective(df_before, df, "toss_decision")
        # If it's something like "defending", ignore it since toss_decision is only bat/field

    # ── 9. Innings Number ─────────────────────────────────────────────────────
    innings = filters.get("innings")
    if innings is not None and "innings" in df.columns:
        df_before = df
        df = df[df["innings"] == int(innings)]
        _assert_filter_effective(df_before, df, "innings")

    # ── 10. Series / Tournament (strict whitelist JOIN) ────────────────────────
    series = filters.get("series")
    if series and "competition" in df.columns:
        comp_vals = resolve_competition(series.lower())
        if comp_vals:
            df_before = df
            df = df[df["competition"].isin(comp_vals)]
            _assert_filter_effective(df_before, df, "competition")
        elif EXECUTION_MODE == "STRICT":
            raise ValueError(
                f"[STRICT MODE] Series '{series}' not in COMPETITION_MAP whitelist. "
                f"Add canonical competition names to COMPETITION_MAP in feature_registry.py."
            )

    # ── 11. Home/Away (Role-aware logic) ─
    home_away = filters.get("home_away")
    if home_away and "home_team" in df.columns and "batting_team" in df.columns and "bowling_team" in df.columns:
        is_batting_role = filters.get("_is_batting_role", True)
        subject_team_col = "batting_team" if is_batting_role else "bowling_team"
        if home_away.lower() == "home":
            df_before = df
            if "neutral_venue" in df.columns:
                df = df[(df["neutral_venue"].astype(int) == 0) & (df[subject_team_col] == df["home_team"])]
            else:
                df = df[df[subject_team_col] == df["home_team"]]
            _assert_filter_effective(df_before, df, "home_team")
        elif home_away.lower() == "away":
            df_before = df
            if "neutral_venue" in df.columns:
                df = df[(df["neutral_venue"].astype(int) == 0) & (df[subject_team_col] != df["home_team"])]
            else:
                df = df[df[subject_team_col] != df["home_team"]]
            _assert_filter_effective(df_before, df, "home_team")

    # ── 12. Neutral Venue (Fix 6) ─────────────────────────────────────────────
    neutral = filters.get("neutral_venue")
    if neutral is not None and "neutral_venue" in df.columns:
        neutral_int = int(bool(neutral))
        df_before = df
        df = df[df["neutral_venue"].astype(int) == neutral_int]
        _assert_filter_effective(df_before, df, "neutral_venue")
    elif neutral is not None and "neutral_venue" not in df.columns:
        if EXECUTION_MODE == "STRICT":
            print("    [STRICT-WARN] neutral_venue column missing in DB — filter skipped.")

    # ── 13-15. Batting identity handled before this call (df already filtered) ─
    # (df was pre-filtered by df[df['batter'] == canonical_name] or equivalent)

    # ── 18. Dismissal Type ────────────────────────────────────────────────────
    dismissal_type = filters.get("dismissal_type")
    if dismissal_type and "wicket_type" in df.columns and is_batting:
        df_before = df
        df = df[df["wicket_type"].str.lower() == dismissal_type.lower()]
        _assert_filter_effective(df_before, df, "wicket_type")

    # ── 21. Batting Position ─────────────────────────────────────────────────
    batting_pos = filters.get("batting_position")
    if batting_pos is not None and "batting_position" in df.columns:
        df_before = df
        df = df[df["batting_position"] == int(batting_pos)]
        _assert_filter_effective(df_before, df, "batting_position")

    # ── 22. Non-Striker ───────────────────────────────────────────────────────
    non_striker = filters.get("non_striker")
    if non_striker and "non_striker" in df.columns:
        ns_res = _resolve_player(engine, non_striker)
        if ns_res:
            ns_canonical = ns_res["canonical_name"]
            ns_aliases = _get_scorecard_aliases(
                "non_striker", ns_canonical, engine,
                SQLITE_DB
            )
            if ns_aliases:
                print(f"    [OK] non_striker aliases for '{ns_canonical}': {ns_aliases}")
                df_before = df
                df = df[df["non_striker"].isin(ns_aliases)]
                _assert_filter_effective(df_before, df, "non_striker")
            else:
                # Fallback: canonical string match
                df_before = df
                df = df[df["non_striker"].str.lower() == ns_canonical.lower()]
                _assert_filter_effective(df_before, df, "non_striker")

    # ── 24-25. Opposition (for batting filters) ────────────────────────────────
    opposition = filters.get("opposition")
    if opposition:
        if is_batting and "bowling_team" in df.columns:
            df_before = df
            df = df[df["bowling_team"].str.lower().str.contains(opposition.lower(), na=False)]
            _assert_filter_effective(df_before, df, "bowling_team")
        elif not is_batting and "batting_team" in df.columns:
            df_before = df
            df = df[df["batting_team"].str.lower().str.contains(opposition.lower(), na=False)]
            _assert_filter_effective(df_before, df, "batting_team")

    # ── 26-28. Bowler identity / type / hand ──────────────────────────────
    bowler_filter         = filters.get("bowler")
    bowler_type           = filters.get("bowler_type")
    bowler_hand           = filters.get("bowler_hand")
    batter_vs_bowler_type = filters.get("batter_vs_bowler_type")
    batter_vs_specific    = filters.get("batter_vs_bowler")

    if bowler_filter and is_batting and "bowler" in df.columns:
        b_res = _resolve_player(engine, bowler_filter)
        if b_res:
            b_canonical = b_res["canonical_name"]
            b_aliases = _get_scorecard_aliases(
                "bowler", b_canonical, engine,
                SQLITE_DB
            )
            if b_aliases:
                print(f"    [OK] bowler aliases for '{b_canonical}': {b_aliases}")
                df_before = df
                df = df[df["bowler"].isin(b_aliases)]
                _assert_filter_effective(df_before, df, "bowler")
            else:
                df_before = df
                df = df[df["bowler"].str.lower() == b_canonical.lower()]
                _assert_filter_effective(df_before, df, "bowler")
        filters.setdefault("batter_vs_bowler", bowler_filter)

    if (bowler_type or bowler_hand or batter_vs_bowler_type) and is_batting:
        # ── Fast path: use pre-computed columns from 38-col schema ────────────
        if bowler_type and "bowler_type" in df.columns:
            df_before = df
            df = df[df["bowler_type"].str.lower() == bowler_type.lower()]
            _assert_filter_effective(df_before, df, "bowler_type")
        if bowler_hand and "bowler_hand" in df.columns:
            df_before = df
            df = df[df["bowler_hand"].str.lower() == bowler_hand.lower()]
            _assert_filter_effective(df_before, df, "bowler_hand")
        if batter_vs_bowler_type:
            bvbt_l = batter_vs_bowler_type.lower()
            # If we are a Batter looking at the Bowler's type:
            if is_batting and "bowler_type" in df.columns:
                df_before = df
                df = df[df["bowler_type"].str.lower().str.contains(bvbt_l, na=False)]
                _assert_filter_effective(df_before, df, "bowler_type")
            # If we are a Bowler → use pre-computed batter_hand column (ingestion-time feature)
            elif not is_batting and "batter_hand" in df.columns:
                if "left" in bvbt_l:
                    df_before = df
                    df = df[df["batter_hand"].str.lower() == "left"]
                    _assert_filter_effective(df_before, df, "batter_hand")
                elif "right" in bvbt_l:
                    df_before = df
                    df = df[df["batter_hand"].str.lower() == "right"]
                    _assert_filter_effective(df_before, df, "batter_hand")
            # SAFE mode only: runtime lookup fallback (slow but correct)
            elif not is_batting and EXECUTION_MODE != "STRICT":
                def _batter_hand_check(b_name: str) -> bool:
                    meta = engine.resolve_for_ingestion(b_name)
                    if not meta: return False
                    b_style = meta.get("batting_style", "").lower()
                    if "left" in bvbt_l: return "left" in b_style
                    if "right" in bvbt_l: return "right" in b_style
                    return False
                df_before = df
                df = df[df["batter"].apply(_batter_hand_check)]
                _assert_filter_effective(df_before, df, "batter")
            elif not is_batting and EXECUTION_MODE == "STRICT":
                raise FeatureMissingError(
                    "batter_hand",
                    "Cannot filter bowler vs LHB/RHB without pre-computed batter_hand column. "
                    "Run: python scripts/migrate_enrich_deliveries.py"
                )

        # ── Slow path: legacy schema without pre-computed bowler columns ───────
        missing = (
            (bowler_type and "bowler_type" not in df.columns) or
            (bowler_hand and "bowler_hand" not in df.columns)
        )
        if missing and "bowler" in df.columns:
            for b in df["bowler"].unique():
                if b not in _BOWLER_STYLE_CACHE:
                    _bowler_style(engine, b)

            def _legacy_type_check(b_name: str) -> bool:
                btype, bhand = _BOWLER_STYLE_CACHE.get(b_name, ("Unknown", "Unknown"))
                if bowler_type and "bowler_type" not in df.columns:
                    if btype != bowler_type:
                        return False
                if bowler_hand and "bowler_hand" not in df.columns:
                    if bhand != bowler_hand and bhand != "Unknown":
                        return False
                return True

            df_before = df
            df = df[df["bowler"].apply(_legacy_type_check)]
            _assert_filter_effective(df_before, df, "bowler")

    # ── 37. Batter vs. specific bowler (head-to-head) ─────────────────────────
    if batter_vs_specific and "bowler" in df.columns:
        res = _resolve_player(engine, batter_vs_specific)
        if res:
            hvh_canonical = res["canonical_name"]
            hvh_aliases = _get_scorecard_aliases(
                "bowler", hvh_canonical, engine,
                SQLITE_DB
            )
            if hvh_aliases:
                print(f"    [OK] batter_vs_bowler aliases for '{hvh_canonical}': {hvh_aliases}")
                df_before = df
                df = df[df["bowler"].isin(hvh_aliases)]
                _assert_filter_effective(df_before, df, "bowler")
            else:
                df_before = df
                df = df[df["bowler"].str.lower() == hvh_canonical.lower()]
                _assert_filter_effective(df_before, df, "bowler")

    # ── 34. Over Number / Range (0-indexed in Cricsheet) ───────────────────
    over_range = filters.get("over_range")
    over_num = filters.get("over_number")
    
    if over_range and "over" in df.columns:
        df_before = df
        df = df[(df["over"] >= over_range[0]) & (df["over"] <= over_range[1])]
        _assert_filter_effective(df_before, df, "over")
    elif over_num is not None and "over" in df.columns:
        df_before = df
        df = df[df["over"] == int(over_num)]
        _assert_filter_effective(df_before, df, "over")

    # ── 35. Match Phase ────────────────────────────────────────────────────
    match_phase = filters.get("match_phase")
    if match_phase:
        if "match_phase" in df.columns:
            # Fast: use pre-computed column
            df_before = df
            df = df[df["match_phase"].str.lower() == match_phase.lower()]
            _assert_filter_effective(df_before, df, "match_phase")
        elif "over" in df.columns:
            # Vectorized rule-based match phase classification
            df_before = df
            overs = df["over"].astype(int)
            
            # Default bins (Test/Generic)
            phase_series = pd.cut(overs, bins=[-1, 5, 14, 100], labels=["Powerplay", "Middle", "Death"])
            
            # Refine based on match_type if available
            if "match_type" in df.columns:
                mt = df["match_type"].str.lower()
                # T20 logic (0-5, 6-14, 15-19)
                t20_mask = mt.str.contains("t20")
                if t20_mask.any():
                    phase_series = phase_series.mask(t20_mask, pd.cut(overs, bins=[-1, 5, 14, 20], labels=["Powerplay", "Middle", "Death"]))
                
                # ODI logic (0-9, 10-39, 40-49)
                odi_mask = mt.str.contains("odi|odm")
                if odi_mask.any():
                    phase_series = phase_series.mask(odi_mask, pd.cut(overs, bins=[-1, 9, 39, 50], labels=["Powerplay", "Middle", "Death"]))

            df = df[phase_series.astype(str).str.lower() == match_phase.lower()]
            _assert_filter_effective(df_before, df, "match_phase")

    return df


# ── Metric Calculation ────────────────────────────────────────────────────────

def _batting_metrics(df: pd.DataFrame, metric_str: str) -> dict | None:
    """Compute a batting-side metric from filtered deliveries."""
    if df.empty:
        return None
    # Prefer deterministic formula registry for key metrics
    metric_canonical = None
    ms = metric_str.lower().strip()
    if "batting average" in ms or (ms == "average"):
        metric_canonical = "Batting Average"
    elif "strike rate" in ms:
        metric_canonical = "Strike Rate"
    elif "total runs" in ms or ms == "runs":
        metric_canonical = "Total Runs"
    elif "high score" in ms:
        metric_canonical = "High Score"
    elif "milestone" in ms or "centur" in ms or "fift" in ms:
        metric_canonical = "Milestones"
    elif "dot" in ms:
        metric_canonical = "Dot Ball %"
    elif "boundary" in ms:
        metric_canonical = "Boundary %"
    elif "balls" in ms:
        metric_canonical = "Balls Faced"

    if metric_canonical:
        res = compute_metric(metric_canonical, df)
        if res:
            if res.get("status") == "insufficient_data":
                return res
            return res

    # Fallback to legacy behavior for metrics not yet in registry
    total_runs = int(df["runs_batter"].sum()) if "runs_batter" in df.columns else 0
    wides = int(df["extras_wides"].sum()) if "extras_wides" in df.columns else 0
    balls_faced = len(df) - wides
    dismissals = int(df["is_wicket"].sum()) if "is_wicket" in df.columns else 0
    innings_count = int(df["match_id"].nunique()) if "match_id" in df.columns else (1 if balls_faced > 0 else 0)
    meta = {"runs": total_runs, "balls": balls_faced, "dismissals": dismissals, "innings": innings_count, "formula": "Runs"}
    return {"value": float(total_runs), "meta": meta}


def _bowling_metrics(df: pd.DataFrame, metric_str: str) -> dict | None:
    """Compute a bowling-side metric from filtered deliveries."""
    if df.empty:
        return None
    # Prefer deterministic formula registry for key metrics
    ms = metric_str.lower().strip()
    metric_canonical = None
    if "economy" in ms:
        metric_canonical = "Economy Rate"
    elif "wickets" in ms:
        metric_canonical = "Wickets"
    elif "strike rate" in ms:
        metric_canonical = "Bowling Strike Rate"
    elif "average" in ms:
        metric_canonical = "Bowling Average"
    elif "dots" in ms:
        metric_canonical = "Dots Forced"
    elif "extras" in ms:
        metric_canonical = "Extras Conceded"
    
    if metric_canonical:
        res = compute_metric(metric_canonical, df)
        if res:
            if res.get("status") == "insufficient_data":
                return res
            return res

    # Fallback: legacy minimal metadata
    wickets = int(df["is_bowler_wicket"].sum()) if "is_bowler_wicket" in df.columns else 0
    runs_total = int(df["runs_total"].sum()) if "runs_total" in df.columns else 0
    return {"value": float(runs_total), "meta": {"wickets": wickets, "runs_total": runs_total, "formula": "Runs Total"}}


def calculate_real_value(df: pd.DataFrame, canonical_subject: str,
                         metric: str, filters: dict,
                         engine: IdentityEngine) -> dict | None:
    """
    Main calculation entry point.

    1. Determine whether this is a batting or bowling query.
    2. Pre-filter by subject.
    3. Apply all 38 filter dimensions.
    4. Compute the requested metric.
    """
    metric_l = (metric or "").lower()

    # ── Determine query mode ──────────────────────────────────────────────────
    # 1. Explicitly bowling?
    if any(m in metric_l for m in _BOWLING_SPECIFIC):
        is_batting = False
    # 2. Explicitly batting?
    elif any(m in metric_l for m in _BATTING_SPECIFIC):
        is_batting = True
    # 3. Ambiguous? (e.g., "average") -> Check player's primary role
    elif any(m in metric_l for m in _AMBIGUOUS_METRICS):
        subj_res = engine.resolve_for_ingestion(canonical_subject)
        role = subj_res.get("primary_role", "Unknown") if subj_res else "Unknown"
        is_batting = "Bowler" not in role
    # 4. Fallback
    else:
        is_batting = True

    # ── Subject pre-filter ────────────────────────────────────────────────────
    subject_col = "batter" if is_batting else "bowler"
    if subject_col not in df.columns:
        print(f"    [FAIL] Column '{subject_col}' not found in dataset.")
        return None

    df_sub = df.copy()
    if df_sub.empty:
        print(f"    [FAIL] No deliveries found for '{canonical_subject}' as {subject_col}.")
        return None

    print(f"    [OK] Pre-filter rows for '{canonical_subject}': {len(df_sub):,}")

    # ── Apply 38-dimensional filters ──────────────────────────────────────────
    df_filt = apply_filters(df_sub, filters, engine, is_batting)

    if df_filt.empty:
        print("    [FAIL] No data after applying filters.")
        return None

    print(f"    [OK] Post-filter rows: {len(df_filt):,}")

    # ── Calculate metric ──────────────────────────────────────────────────────
    if is_batting:
        return _batting_metrics(df_filt, metric_l)
    else:
        return _bowling_metrics(df_filt, metric_l)


import sqlite3

# ── CSV / SQLite loader ────────────────────────────────────────────────────

def _load_subject_dataframe(subject_col: str, canonical_subject: str, engine: IdentityEngine, 
                            metric: str = "average", filters: dict = {}) -> pd.DataFrame | None:
    db_path = SQLITE_DB
    
    # Priority: DuckDB > SQLite
    use_duckdb = os.path.exists(DUCKDB_PATH)
    
    if not use_duckdb and not os.path.exists(db_path):
        print(f"    [FAIL] Dataset (DuckDB/SQLite) not found.")
        return None

    # Performance Optimization: Scorecard Alias Caching
    matched_aliases = _get_scorecard_aliases(subject_col, canonical_subject, engine, db_path)
    if not matched_aliases:
        return None

    print(f"    [OK] Matched scorecard aliases: {matched_aliases}")
    
    # Performance Optimization: Column Projection
    required_cols = _get_required_columns(metric, filters)
    cols_str = ", ".join(required_cols)
    
    # ── Performance Optimization: Predicate Pushdown (SQL filtering) ──────────
    where_clauses = [f"{subject_col} IN ({','.join('?' for _ in matched_aliases)})"]
    params = list(matched_aliases)
    
    # Safe pushdown candidates: country, innings, match_phase, batting_position, venue_name
    if filters.get("_is_comparison_half"):
        pass # Skip country pushdown -- handled in Python apply_filters
    elif filters.get("country"):
        where_clauses.append("LOWER(country) = ?")
        params.append(filters["country"].lower())
    
    if filters.get("innings") is not None:
        where_clauses.append("innings = ?")
        params.append(int(filters["innings"]))
        
    if filters.get("match_phase"):
        where_clauses.append("LOWER(match_phase) = ?")
        params.append(filters["match_phase"].lower())
        
    if filters.get("batting_position") is not None:
        where_clauses.append("batting_position = ?")
        params.append(int(filters["batting_position"]))
        
    if filters.get("venue_name"):
        where_clauses.append("venue_name LIKE ?")
        params.append(f"%{filters['venue_name']}%")

    # Push competition/format filters into SQL for better selectivity before fetch_df
    fmt_raw = filters.get("format")
    if fmt_raw:
        comp_vals = resolve_competition(fmt_raw.lower())
        mt_vals   = resolve_format(fmt_raw.lower())
        if comp_vals and "competition" in required_cols:
            placeholders = ",".join(["?" for _ in comp_vals])
            where_clauses.append(f"competition IN ({placeholders})")
            params.extend(comp_vals)
        elif mt_vals and "match_type" in required_cols:
            placeholders = ",".join(["?" for _ in mt_vals])
            where_clauses.append(f"match_type IN ({placeholders})")
            params.extend(mt_vals)

    query = f"SELECT {cols_str} FROM deliveries WHERE {' AND '.join(where_clauses)}"

    try:
        if use_duckdb:
            import duckdb
            con = duckdb.connect(DUCKDB_PATH, read_only=True)
            try:
                con.execute("SET memory_limit='256MB'")
                con.execute("SET threads=1")
            except Exception:
                pass
            print(f"    [OK] Powered by DuckDB: {len(where_clauses)-1} filters pushed.")
            df_sub = con.execute(query, params).fetch_df()
            con.close()
        else:
            import sqlite3
            con = sqlite3.connect(db_path, timeout=30.0)
            print(f"    [OK] Powered by SQLite: {len(where_clauses)-1} filters pushed.")
            df_sub = pd.read_sql(query, con, params=tuple(params))
            con.close()
        
        # ensure datatypes match old csv loader
        for col in ["is_wicket", "is_bowler_wicket", "runs_batter", "runs_total"]:
            if col in df_sub.columns:
                df_sub[col] = df_sub[col].astype("Int64")

        return df_sub
    except Exception as exc:
        print(f"    [FAIL] Error loading subset from {('DuckDB' if use_duckdb else 'SQLite')}: {exc}")
        return None

# ── Filter set → legacy filter dict conversion ───────────────────────────────

def _filterset_to_dict(fs: FilterSet) -> dict:
    """Convert a QueryPlanner FilterSet back to the legacy filter dict format
    that apply_filters() accepts. Bridge for backwards compatibility."""
    d: dict = {"_is_batting_role": fs.is_batting}

    if fs.match_types and not fs.competitions:
        d["_match_types"] = fs.match_types
    if fs.competitions:
        d["_competitions"] = fs.competitions

    d["country"]          = fs.country
    d["city"]             = fs.city
    d["venue_name"]       = fs.venue_name
    d["day_night"]        = fs.day_night
    d["innings"]          = fs.innings
    d["match_phase"]      = fs.match_phase
    d["over_number"]      = fs.over_number
    d["over_range"]       = fs.over_range   # Fix: forward over_range filter
    d["batting_position"] = fs.batting_position
    d["opposition"]       = fs.opposition
    d["home_away"]        = fs.home_away
    d["toss_decision"]    = fs.toss_decision
    d["bowler_type"]      = fs.bowler_type
    d["bowler_hand"]      = fs.bowler_hand
    d["ball_type"]        = fs.ball_type
    # batter_hand used by bowler-vs-LHB logic
    d["batter_vs_bowler_type"] = (
        f"{fs.batter_hand}-hand" if fs.batter_hand else None
    )
    # Season bounds: store parsed values
    if fs.season_gte:
        d["season"] = f"since {fs.season_gte}"
    elif fs.season_lte:
        d["season"] = f"before {fs.season_lte}"
    elif fs.season_eq:
        d["season"] = str(fs.season_eq)

    # Region flags for comparative queries
    if getattr(fs, "_asia_filter", False):
        d["_region"] = "Asia"
    elif getattr(fs, "_non_asia_filter", False):
        d["_region"] = "Outside Asia"

    return {k: v for k, v in d.items() if v is not None}


def apply_filters_from_plan(df: pd.DataFrame, fs: FilterSet,
                             engine: IdentityEngine) -> pd.DataFrame:
    """
    Apply a FilterSet (from QueryPlanner) to a DataFrame.
    Handles format/competition filtering via exact match, then
    delegates remaining filters to apply_filters().
    """
    # ── Competition / Format (strict) ────────────────────────────────────────
    if fs.competitions and "competition" in df.columns:
        df = df[df["competition"].isin(fs.competitions)]
    elif fs.match_types and "match_type" in df.columns:
        if getattr(fs, "_is_t20i", False) and "batting_team" in df.columns and "bowling_team" in df.columns:
            _intl = ['Pakistan', 'India', 'Australia', 'England', 'South Africa', 'New Zealand', 
                     'West Indies', 'Sri Lanka', 'Bangladesh', 'Afghanistan', 'Zimbabwe', 'Ireland']
            df = df[
                (df["match_type"] == "IT20") |
                ((df["match_type"] == "T20") & df["batting_team"].isin(_intl) & df["bowling_team"].isin(_intl))
            ]
        else:
            df = df[df["match_type"].isin(fs.match_types)]

    # ── Region (comparative) ─────────────────────────────────────────────────
    if getattr(fs, "_asia_filter", False) and "country" in df.columns:
        df = df[df["country"].isin(_ASIA_COUNTRIES)]
    elif getattr(fs, "_non_asia_filter", False) and "country" in df.columns:
        df = df[~df["country"].isin(_ASIA_COUNTRIES)]

    # ── ball_type (if populated in DB) ────────────────────────────────────────
    if fs.ball_type and "ball_type" in df.columns:
        df = df[df["ball_type"] == fs.ball_type]
    elif fs.ball_type and "ball_type" not in df.columns:
        print(f"    [WARN] ball_type filter '{fs.ball_type}' skipped — column not in DB.")
        fs.ball_type = None  # clear so it doesn't affect downstream

    # ── Delegate remaining filters ────────────────────────────────────────────
    legacy_dict = _filterset_to_dict(fs)
    # Remove keys already handled above by apply_filters_from_plan
    # 'format' and 'series' are handled via fs.match_types / fs.competitions
    for key in ["_match_types", "_competitions", "_region", "ball_type", "format", "series"]:
        legacy_dict.pop(key, None)

    return apply_filters(df, legacy_dict, engine, fs.is_batting)


# ── Single / Comparison Execution ─────────────────────────────────────────────

def _standard_output(real_data: dict, metric: str, canonical: str,
                     fs: FilterSet, claimed_val: float,
                     preds: dict, insight_text: str | None,
                     df_full: pd.DataFrame, is_batting: bool) -> dict:
    """Build the production-grade standard output dict."""
    real_val  = real_data["value"]
    real_meta = real_data["meta"]
    # Extraction logic for sample size (handles both legacy and registry formats)
    sample_balls = real_meta.get("balls")
    if sample_balls is None:
        sample_balls = real_meta.get("sample_size", {}).get("balls", real_meta.get("overs", 0) * 6)
    confidence   = real_meta.get("confidence", min(1.0, sample_balls / 600))

    if claimed_val is None:
        accuracy = 100.0
        verdict, emoji = "Informational", "Info"
    else:
        if real_val == 0:
            accuracy = 100.0 if claimed_val == 0 else 0.0
        else:
            delta = abs(float(claimed_val) - real_val)
            m_lower = metric.lower()

            # Format-Aware Variable Tolerances
            if "average" in m_lower or "economy" in m_lower or "rate" in m_lower:
                # Even a delta of 1.0 is huge for average / economy / strike rate
                error_ratio = (delta / real_val) * 3.0
            elif "runs" in m_lower or "balls" in m_lower or "score" in m_lower:
                # Allow a slightly wider absolute margin (discount first 10.0 runs/balls of delta)
                # but apply a scale factor of 2.5 to the remaining error to drop the tier quickly
                effective_delta = max(0.0, delta - 10.0)
                error_ratio = (effective_delta / real_val) * 2.5
            else:
                error_ratio = delta / real_val

            accuracy = max(0.0, (1.0 - error_ratio) * 100.0)
        accuracy = max(0.0, min(100.0, accuracy))

        # Enforce Strict Compressed Thresholding
        if accuracy >= 99.0:
            verdict, emoji = "VERIFIED_FACT", "VERIFIED_FACT"
        elif accuracy >= 95.0:
            verdict, emoji = "MINOR_DEVIATION", "MINOR_DEVIATION"
        elif accuracy >= 85.0:
            verdict, emoji = "INACCURATE", "INACCURATE"
        else:
            verdict, emoji = "FALSE", "FALSE"

    active = {k: v for k, v in _filterset_to_dict(fs).items()
              if v is not None and not k.startswith("_")}

    return {
        "status":          "ok",
        "verdict":         verdict,
        "emoji":           emoji,
        "accuracy_pct":    round(accuracy, 2),
        "claimed_val":     claimed_val,
        "real_val":        round(real_val, 4),
        "real_meta":       real_meta,
        "metric":          metric,
        "subject":         canonical,
        "filters":         active,
        "sample_size":     int(sample_balls),
        "confidence":      round(confidence, 3),
        "features_used":   [c for c in FEATURE_REGISTRY if c in (fs.__dict__ or {})],
        "execution_mode":  EXECUTION_MODE,
        "predictions":     preds,
        "insight":         insight_text,
    }


def _execute_single_plan(
    plan: ExecutionPlan, engine: IdentityEngine,
    df_full: pd.DataFrame, metric: str, canonical: str,
    skip_predictions: bool = False
) -> dict:
    """Execute a single (non-comparative) plan."""
    fs = plan.primary
    print(f"\n[Phase 4a] Applying FilterSet ({len([v for v in vars(fs).values() if v])} active fields)...")
    df = apply_filters_from_plan(df_full, fs, engine)

    if df.empty:
        # Distinguish: was it the feature that was missing, or genuinely no matches?
        cause = "no_matching_data"
        active_filters = [k for k, v in _filterset_to_dict(fs).items() if v is not None and not k.startswith("_")]
        if active_filters:
            msg = (f"No data for '{canonical}' with filters: {active_filters}. "
                   f"Either the filter values don't exist in the dataset, "
                   f"or the required column is missing.")
        else:
            msg = f"No delivery data found for '{canonical}'."
        
        print(f"    [X] {msg}")
        return {
            "status": cause, "message": msg,
            "subject": canonical, "metric": metric, "filters": active_filters,
            "sample_size": 0, "confidence": 0.0,
            "hint": "Check if derived features (batter_hand, match_phase) are populated.",
            "execution_mode": EXECUTION_MODE,
        }

    print(f"    >> Post-filter rows: {len(df):,}")
    real_data = (
        _batting_metrics(df, metric.lower()) if fs.is_batting
        else _bowling_metrics(df, metric.lower())
    )
    if isinstance(real_data, dict) and real_data.get("status") == "insufficient_data":
        msg = f"Insufficient data to compute '{metric}' for the given filters."
        return {
            "status": "insufficient_data",
            "message": msg,
            "subject": canonical,
            "metric": metric,
            "sample_size": int(real_data.get("meta", {}).get("sample_size", {}).get("balls", 0)),
            "execution_mode": EXECUTION_MODE,
            "real_meta": real_data.get("meta", {}),
        }
    if real_data is None:
        msg = f"Cannot compute '{metric}' — required columns may be absent."
        print(f"    [X] {msg}")
        return {
            "status": "calculation_failed", "message": msg,
            "subject": canonical, "metric": metric,
            "sample_size": len(df), "confidence": 0.0,
            "hint": f"Ensure columns for '{metric}' exist in the deliveries table.",
            "execution_mode": EXECUTION_MODE,
        }

    import sys as _sys, io as _io
    old_stdout = _sys.stdout; _sys.stdout = _io.StringIO()
    try:
        baseline_data = (
            _batting_metrics(df_full, metric.lower()) if fs.is_batting
            else _bowling_metrics(df_full, metric.lower())
        )
    finally:
        _sys.stdout = old_stdout

    insight_text = None
    if baseline_data:
        active = {k: v for k, v in _filterset_to_dict(fs).items()
                  if v is not None and not k.startswith("_")}
        insight_text = generate_insight(canonical, metric, active,
                                        real_data["value"], baseline_data["value"])
        if insight_text:
            print(insight_text)

    preds = {}
    if not skip_predictions:
        preds = _lazy_prediction(df_full, canonical, fs.is_batting, _filterset_to_dict(fs))

    return _standard_output(
        real_data, metric, canonical, fs, plan.claimed_value,
        preds, insight_text, df_full, fs.is_batting
    )


def _execute_comparison_plan(
    plan: ExecutionPlan, engine: IdentityEngine,
    df_full: pd.DataFrame, metric: str, canonical: str
) -> dict:
    """Execute a comparison plan — runs metric twice and returns delta."""
    print(f"\n[Phase 4a] COMPARISON: {plan.split_label_a} vs {plan.split_label_b}")

    results = {}
    for label, fs in [
        (plan.split_label_a, plan.split_a),
        (plan.split_label_b, plan.split_b),
    ]:
        df = apply_filters_from_plan(df_full.copy(), fs, engine)
        if df.empty:
            results[label] = None
            print(f"    >> {label}: no data")
            continue
        rd = _bowling_metrics(df, metric.lower()) if not fs.is_batting else _batting_metrics(df, metric.lower())
        results[label] = round(rd["value"], 4) if rd else None
        sample = len(df)
        print(f"    >> {label}: {results[label]} ({sample:,} rows)")

    val_a = results.get(plan.split_label_a)
    val_b = results.get(plan.split_label_b)
    delta = round(val_a - val_b, 4) if (val_a is not None and val_b is not None) else None

    if delta is not None and delta > 0:
        verdict_str = f"Worse in {plan.split_label_a} by {abs(delta):.2f}"
    elif delta is not None:
        verdict_str = f"Better in {plan.split_label_a} by {abs(delta):.2f}"
    else:
        verdict_str = "Inconclusive"

    print(f"\n  {plan.split_label_a}: {val_a}  |  {plan.split_label_b}: {val_b}  |  Delta: {delta}")
    print(f"  Verdict: {verdict_str}")

    return {
        "status":         "ok",
        "type":           "comparison",
        "metric":         metric,
        "subject":        canonical,
        plan.split_label_a: val_a,
        plan.split_label_b: val_b,
        "delta":          delta,
        "verdict":        verdict_str,
        "execution_mode": EXECUTION_MODE,
    }


    """
    Full 5-phase pipeline:
      1. NL → structured JSON (ai_parser)
      2. Player identity resolution (IdentityEngine)
      3. Dataset load + 38-filter application
      4. Metric calculation
      5. Truth-O-Meter verdict

    Returns a result dict for programmatic use (useful in test suites).
    """
    sep = "-" * 58
    print(f"\n[{sep}]")
    print(f"  CLAIM: \"{claim_string}\"")
    print(f"[{sep}]\n")

    # ─ Phase 1: Parse ─────────────────────────────────────────────────────────
    print("[Phase 1] Semantic Parsing …")
    parsed = parse_claim(claim_string)
    print(f"    [OK] Parsed JSON:\n{json.dumps(parsed, indent=6)}")

    subject      = parsed.get("subject")
    metric       = parsed.get("metric")
    claimed_val  = parsed.get("claimed_value")
    filters      = parsed.get("filters", {})

    # Exploratory Query Defaulting: allow queries without explicit numbers or metrics
    if claimed_val is None:
        claimed_val = 0.0

    if not subject:
        msg = "Could not identify a primary subject (player) from your query."
        print(f"    [FAIL] {msg}")
        return {"status": "error", "message": msg}

    # ─ Phase 2: Identity Resolution ───────────────────────────────────────────
    print("\n[Phase 2] Identity Resolution …")
    engine = _get_engine()
    _load_bowler_db()  # warm the bowler cache

    if engine.players_db.empty:
        print("    [WARN]  Player DB is empty — resolution may fail.")

    # Contextual identity resolution: bias toward batter/bowler based on metric+filters.
    metric_hint = (metric or "").lower()
    prefer_role = ""
    if any(m in metric_hint for m in _BOWLING_SPECIFIC):
        prefer_role = "bowling"
    elif any(m in metric_hint for m in _BATTING_SPECIFIC):
        prefer_role = "batting"
    prefer_bowling_type = ""
    if (filters.get("bowler_type") or filters.get("batter_vs_bowler_type")):
        bt = str(filters.get("bowler_type") or filters.get("batter_vs_bowler_type") or "").lower()
        prefer_bowling_type = "spin" if "spin" in bt else ("pace" if "pace" in bt or "fast" in bt or "seam" in bt else "")
    query_res = engine.resolve(subject, context={
        "prefer_role": prefer_role,
        "prefer_bowling_type": prefer_bowling_type,
        "prefer_country": (filters.get("country") or ""),
    })
    if "needs_disambiguation" in query_res.get("status", ""):
        opts = [f"{c['name']} ({c['meta'].get('country','')})" for c in query_res['candidates']]
        msg = f"Which '{subject}' do you mean? Options: {', '.join(opts)}"
        print(f"    [WARN] {msg}")
        return {"status": "needs_disambiguation", "message": msg, "options": query_res['candidates']}
        
    subj_res = query_res.get("resolved")
    if not subj_res:
        msg = f"Cannot resolve player '{subject}'."
        print(f"    [X] {msg}")
        return {"status": "error", "message": msg}

    canonical = subj_res["canonical_name"]
    print(f"    [OK] Resolved: '{subject}' → '{canonical}'")
    
    if not metric:
        role = subj_res.get("primary_role", "Unknown")
        metric = "Bowling Economy" if "Bowler" in role else "Batting Average"
    if subj_res.get("ambiguous"):
        print(f"    [WARN]  Ambiguous match (confidence {subj_res.get('confidence', 0):.0%}); best guess used.")

    # Also resolve any bowler / non-striker name in filters up-front (Optimization)
    for filter_key in ["bowler", "non_striker", "batter_vs_bowler"]:
        if filters.get(filter_key):
            res = engine.resolve_for_ingestion(filters[filter_key])
            if res:
                filters[filter_key] = res["canonical_name"]

    # ─ Phase 3: Data & Filters ────────────────────────────────────────────────
    print("\n[Phase 3] Dataset Load & Filter Application …")
    
    # Re-calculate is_batting exactly like calculate_real_value does
    metric_l = (metric or "").lower()
    if any(m in metric_l for m in _BOWLING_SPECIFIC):
        is_batting = False
    elif any(m in metric_l for m in _BATTING_SPECIFIC):
        is_batting = True
    elif any(m in metric_l for m in _AMBIGUOUS_METRICS):
        role = subj_res.get("primary_role", "Unknown")
        is_batting = "Bowler" not in role
    else:
        is_batting = True

    subject_col = "batter" if is_batting else "bowler"
    filters["_is_batting_role"] = is_batting # Pass role flag to filters
    df = _load_subject_dataframe(subject_col, canonical, engine, metric=metric, filters=filters)
    if df is None:
        return {"status": "error", "message": "Dataset unavailable."}

    active_filters = {k: v for k, v in filters.items() if v is not None}
    print(f"    [OK] Active filters ({len(active_filters)}/38): {active_filters}")

    # ─ Phase 4: Calculate ─────────────────────────────────────────────────────
    print(f"\n[Phase 4] Calculating '{metric}' …")
    real_data = calculate_real_value(df, canonical, metric, filters, engine)

    if real_data is None:
        msg = f"Not enough data for '{canonical}' with the given filters."
        print(f"    [FAIL] {msg}")
        return {"status": "no_data", "message": msg,
                "subject": canonical, "metric": metric, "filters": filters}
                
    real_val = real_data["value"]
    real_meta = real_data["meta"]

    # ─ Phase 5: Verdict ───────────────────────────────────────────────────────
    print("\n[Phase 5] Truth-O-Meter …")
    print(f"    Claimed : {claimed_val}")
    print(f"    Actual  : {real_val:.4f}")

    if real_val == 0:
        accuracy = 100.0 if claimed_val == 0 else 0.0
    else:
        delta = abs(float(claimed_val) - real_val)
        m_lower = metric.lower()
        if "average" in m_lower or "economy" in m_lower or "rate" in m_lower:
            error_ratio = (delta / real_val) * 3.0
        elif "runs" in m_lower or "balls" in m_lower or "score" in m_lower:
            effective_delta = max(0.0, delta - 10.0)
            error_ratio = (effective_delta / real_val) * 2.5
        else:
            error_ratio = delta / real_val
        accuracy = max(0.0, (1.0 - error_ratio) * 100.0)

    accuracy = max(0.0, min(100.0, accuracy))

    if accuracy >= 99.0:
        verdict, emoji = "VERIFIED_FACT", "[TARGET]"
    elif accuracy >= 95.0:
        verdict, emoji = "MINOR_DEVIATION", "[YES]"
    elif accuracy >= 85.0:
        verdict, emoji = "INACCURATE", "[WARN]"
    else:
        verdict, emoji = "FALSE", "[FAIL]"

    print(f"\n{'='*50}")
    print(f"  VERDICT : {verdict} {emoji}  ({accuracy:.1f}% accurate)")
    print(f"  CLAIMED : {claimed_val}   |   ACTUAL : {real_val:.4f}")
    if is_batting:
        print(f"  MATH    : {real_meta['formula']} (Runs: {real_meta['runs']}, Dismissals: {real_meta['dismissals']}, Innings: {real_meta['innings']})")
    else:
        print(f"  MATH    : {real_meta['formula']} (Wickets: {real_meta['wickets']}, Overs: {real_meta['overs']:.1f}, Innings: {real_meta['innings']})")
    print(f"  METRIC  : {metric}")
    print(f"  SUBJECT : {canonical}")
    print(f"  FILTERS : {active_filters}")
    print(f"{'='*50}\n")

    # ─ Phase 5.5: AI Insights ─────────────────────────────────────────────────
    insight_text = None
    if active_filters:
        # Optimization: Reuse the same loaded 'df' for baseline to avoid I/O
        # Silence baseline print statements temporarily to avoid clutter
        import sys as _sys, io as _io
        old_stdout = _sys.stdout
        _sys.stdout = _io.StringIO()
        try:
            # Re-running calculate_real_value ON THE SAME DF with empty filters
            baseline_data = calculate_real_value(df, canonical, metric, {}, engine)
        finally:
            _sys.stdout = old_stdout
            
        if baseline_data:
            baseline_val = baseline_data["value"]
            insight_text = generate_insight(canonical, metric, active_filters, real_val, baseline_val)
            if insight_text:
                print(insight_text)

    # ─ Phase 6: Prediction ────────────────────────────────────────────────────
    # Passing 'df' which is the full unfiltered subject history df
    # active_filters contain the parsed context to use as Target context
    preds = run_prediction_pipeline(df, canonical, is_batting, active_filters)
    print(format_prediction_output(preds, is_batting))

# ── Main pipeline ──────────────────────────────────────────────────────────────

def validate_parsed_claim(parsed: dict, claim_string: str, skip_predictions: bool = False) -> dict:
    """
    Inner verification pipeline runner using a pre-parsed claim filter payload.
    """
    subject     = parsed.get("subject")
    subject_type = parsed.get("subject_type")
    as_of_date = parsed.get("as_of_date")
    metric      = parsed.get("metric")
    claimed_val = parsed.get("claimed_value")
    filters     = parsed.get("filters", {}) or {}
    # Carry temporal anchor down into the loader/filter layer so date is projected.
    if as_of_date:
        filters["as_of_date"] = as_of_date

    # ── Spatial Filter Guardrail (prevent hallucinated venue country filter) ──
    country_filter = filters.get("country")
    if country_filter:
        country_lower = str(country_filter).lower().strip()
        claim_lower = claim_string.lower()
        
        # 1. Nullify if it matches format/metric keywords
        format_keywords = {"test", "tests", "odi", "odis", "t20", "t20s", "t20i", "t20is"}
        if country_lower in format_keywords:
            filters["country"] = None
            if "filters" in parsed and parsed["filters"]:
                parsed["filters"]["country"] = None
            print(f"    [GUARDRAIL] Nullified format-matching country filter: '{country_filter}'")
        else:
            # 2. Check if the country (or its common adjective/adverb form) is mentioned in the claim
            COUNTRY_KEYWORDS = {
                "india": ["india", "indian"],
                "australia": ["australia", "australian"],
                "england": ["england", "english"],
                "south africa": ["south africa", "south african", "s. africa"],
                "new zealand": ["new zealand", "kiwi", "nz"],
                "pakistan": ["pakistan", "pakistani"],
                "sri lanka": ["sri lanka", "sri lankan"],
                "west indies": ["west indies", "windies", "caribbean"],
                "bangladesh": ["bangladesh", "bangladeshi"],
                "afghanistan": ["afghanistan", "afghan"],
                "zimbabwe": ["zimbabwe", "zimbabwean"],
                "ireland": ["ireland", "irish"],
                "scotland": ["scotland", "scottish"],
                "netherlands": ["netherlands", "dutch"],
                "nepal": ["nepal", "nepalese"],
                "oman": ["oman", "omani"],
                "uae": ["uae", "emirati", "united arab emirates"],
                "usa": ["usa", "united states", "america", "american"],
                "namibia": ["namibia", "namibian"],
                "canada": ["canada", "canadian"],
                "papua new guinea": ["papua new guinea", "png"],
            }
            
            is_mentioned = False
            if country_lower in COUNTRY_KEYWORDS:
                for kw in COUNTRY_KEYWORDS[country_lower]:
                    if kw in claim_lower:
                        is_mentioned = True
                        break
            else:
                if country_lower in claim_lower:
                    is_mentioned = True
                elif len(country_lower) > 4 and country_lower[:-1] in claim_lower:
                    is_mentioned = True
            
            if not is_mentioned:
                filters["country"] = None
                if "filters" in parsed and parsed["filters"]:
                    parsed["filters"]["country"] = None
                print(f"    [GUARDRAIL] Nullified hallucinated country filter: '{country_filter}'")

    # ── Metric normalisation: override LLM if user explicitly stated bowling/batting ──
    claim_lower = claim_string.lower()
    if "bowling average" in claim_lower:
        metric = "Bowling Average"
    elif "bowling economy" in claim_lower or ("economy" in claim_lower and "batting" not in claim_lower):
        metric = "Economy Rate"
    elif "bowling strike rate" in claim_lower:
        metric = "Bowling Strike Rate"
    elif "batting average" in claim_lower:
        metric = "Batting Average"
    elif "batting strike rate" in claim_lower:
        metric = "Batting Strike Rate"
    elif "strike rate" in claim_lower and "batting" not in claim_lower:
        metric = "Strike Rate"
    elif "wickets" in claim_lower:
        metric = "Wickets"

    # ── Filter inference + normalization (reduce drift vs reference sites) ──
    # If user says "World Cups" but parser doesn't set a series, enforce it.
    if ("world cup" in claim_lower) and not filters.get("series"):
        # Use strict whitelist key from COMPETITION_MAP
        filters["series"] = "world cup"

    # Normalize day/night phrasing
    if ("day night" in claim_lower or "day-night" in claim_lower) and not filters.get("day_night"):
        filters["day_night"] = "day-night"

    if not subject:
        msg = "Could not identify a primary subject (player) from your query."
        print(f"    [X] {msg}")
        return {"status": "error", "message": msg, "execution_mode": EXECUTION_MODE}

    # Subject anchoring: prevent trying to resolve team/country aggregates as a player
    if subject_type in {"team", "country"}:
        msg = f"SubjectTypeMismatch: query subject is '{subject_type}', but this pipeline currently supports player subjects only."
        return {"status": "subject_type_mismatch", "message": msg, "execution_mode": EXECUTION_MODE}

    # ─ Phase 2: Identity Resolution ────────────────────────────────────────────
    print("\n[Phase 2] Identity Resolution ...")
    engine = _get_engine()
    _load_bowler_db()

    # Contextual identity resolution: bias toward batter/bowler based on metric+filters.
    metric_hint = (metric or "").lower()
    prefer_role = ""
    if any(m in metric_hint for m in _BOWLING_SPECIFIC):
        prefer_role = "bowling"
    elif any(m in metric_hint for m in _BATTING_SPECIFIC):
        prefer_role = "batting"
    prefer_bowling_type = ""
    if (filters.get("bowler_type") or filters.get("batter_vs_bowler_type")):
        bt = str(filters.get("bowler_type") or filters.get("batter_vs_bowler_type") or "").lower()
        prefer_bowling_type = "spin" if "spin" in bt else ("pace" if "pace" in bt or "fast" in bt or "seam" in bt else "")
    query_res = engine.resolve(subject, context={
        "prefer_role": prefer_role,
        "prefer_bowling_type": prefer_bowling_type,
        "prefer_country": (filters.get("country") or ""),
    })
    if query_res.get("status") == "needs_disambiguation":
        opts = [f"{c['name']} ({c['meta'].get('country', '')})" for c in query_res.get("candidates", [])]
        msg = f"Which '{subject}' do you mean? Options: {', '.join(opts)}"
        print(f"    [DISAMBIG] {msg}")
        return {"status": "needs_disambiguation", "message": msg,
                "options": query_res.get("candidates", []), "execution_mode": EXECUTION_MODE}

    subj_res = query_res.get("resolved")
    if not subj_res:
        msg = f"Cannot resolve player '{subject}'."
        print(f"    [X] {msg}")
        return {"status": "error", "message": msg, "execution_mode": EXECUTION_MODE}

    canonical = subj_res["canonical_name"]
    print(f"    >> Resolved: '{subject}' -> '{canonical}'")

    if not metric:
        role = subj_res.get("primary_role", "Unknown")
        metric = "Bowling Economy" if "Bowler" in role else "Batting Average"
        print(f"    >> Metric defaulted to '{metric}' based on player role.")

    # Resolve any filter-level player names
    for fk in ["bowler", "non_striker", "batter_vs_bowler"]:
        if filters.get(fk):
            res = engine.resolve_for_ingestion(filters[fk])
            if res:
                filters[fk] = res["canonical_name"]

    try:
        # ─ Phase 3: Query Planning ─────────────────────────────────────────────
        print("\n[Phase 3] Query Planning ...")
        planner = QueryPlanner()
        plan = planner.build(parsed, canonical, subj_res, metric, claim_string)
        print(f"    >> Plan type: {plan.type.upper()} | Mode: {plan.execution_mode}")

        # ─ Phase 4: Data Load ──────────────────────────────────────────────────
        print("\n[Phase 4] Dataset Load ...")
        is_batting = (
            plan.primary.is_batting if plan.type == "single"
            else plan.split_a.is_batting
        )
        subject_col = "batter" if is_batting else "bowler"

        # Build legacy filter dict for _load_subject_dataframe (alias resolution)
        legacy_filters = {**filters, "_is_batting_role": is_batting}
        if plan.type == "comparison":
            legacy_filters["_is_comparison_half"] = True
        df_full = _load_subject_dataframe(
            subject_col, canonical, engine, metric=metric, filters=legacy_filters
        )
        if df_full is None:
            return {"status": "error", "message": "Dataset unavailable.", "execution_mode": EXECUTION_MODE}

        print(f"    >> Loaded {len(df_full):,} rows for '{canonical}'.")

        # Temporal anchor: truncate as-of date to avoid future data polluting historical claims
        if as_of_date:
            before = len(df_full)
            df_full = _truncate_as_of(df_full, as_of_date)
            print(f"    >> as_of_date={as_of_date}: {before:,} → {len(df_full):,} rows")

        integrity_warnings = _validate_dataset_integrity(df_full, canonical)
        for _, msg in integrity_warnings.items():
            print(f"    [INTEGRITY-WARN] {msg}")

        # ─ Phase 5: Execution ──────────────────────────────────────────────────
        if plan.type == "comparison":
            result = _execute_comparison_plan(plan, engine, df_full, metric, canonical)
        else:
            result = _execute_single_plan(plan, engine, df_full, metric, canonical, skip_predictions=skip_predictions)
    except FeatureMissingError as e:
        msg = str(e)
        print(f"    [X] {msg}")
        return {"status": "no_data", "message": msg, "execution_mode": EXECUTION_MODE}
    except ValueError as e:
        msg = str(e)
        print(f"    [X] {msg}")
        return {"status": "no_data", "message": msg, "execution_mode": EXECUTION_MODE}
    except Exception as e:
        msg = str(e)
        print(f"    [X] {msg}")
        return {"status": "error", "message": msg, "execution_mode": EXECUTION_MODE}

    # ─ Phase 6: Print verdict ──────────────────────────────────────────────────
    if result.get("status") == "ok":
        rv = result["real_val"]
        print(f"\n{'='*50}")
        if result.get("verdict") == "Informational":
            print(f"  VERDICT : Informational")
            print(f"  VALUE   : {rv:.4f}")
        else:
            print(f"  VERDICT : {result['verdict']}  ({result['accuracy_pct']:.1f}% accurate)")
            print(f"  CLAIMED : {claimed_val}   |   ACTUAL : {rv:.4f}")
        print(f"  METRIC  : {metric}")
        print(f"  SUBJECT : {canonical}")
        print(f"  SAMPLE  : {result['sample_size']:,} balls | CONFIDENCE: {result['confidence']:.0%}")
        print(f"  FILTERS : {result['filters']}")
        print(f"  MODE    : {EXECUTION_MODE}")
        if result.get("real_meta", {}).get("warning"):
            print(f"  WARNING : {result['real_meta']['warning']}")
        print(f"{'='*50}\n")

    # Ensure subject and metric are populated on the result dict for single claims
    result.setdefault("subject", canonical)
    result.setdefault("metric", metric)
    result.setdefault("claimed_value", claimed_val)
    return result


def validate_claim(claim_string: str, skip_predictions: bool = False) -> dict:
    """
    Full production pipeline with support for both single and multi-claim inputs.
    """
    sep = "-" * 58
    print(f"\n[{sep}]")
    print(f"  CLAIM: \"{claim_string}\"")
    print(f"[{sep}]\n")

    # ─ Phase 1: Parse ──────────────────────────────────────────────────────────
    print("[Phase 1] Semantic Parsing via parse_paragraph...")
    from scripts.analysis.ai_parser import parse_paragraph
    
    try:
        parsed_list = parse_paragraph(claim_string)
    except Exception as e:
        msg = f"Failed to parse claim paragraph: {e}"
        print(f"    [X] {msg}")
        return {"status": "error", "message": msg, "execution_mode": EXECUTION_MODE}

    if not parsed_list:
        msg = "No structural claims could be extracted from the input text."
        print(f"    [X] {msg}")
        return {"status": "error", "message": msg, "execution_mode": EXECUTION_MODE}

    print(f"    >> Parsed {len(parsed_list)} claims.")

    # Proceed based on count of decomposed claims
    if len(parsed_list) == 1:
        # Backward compatibility mode for single claims
        parsed = parsed_list[0]
        print(f"    >> Parsed JSON:\n{json.dumps(parsed, indent=6)}")
        return validate_parsed_claim(parsed, claim_string, skip_predictions=skip_predictions)
    else:
        # Multi-claim mode
        verdicts = []
        for idx, p in enumerate(parsed_list, 1):
            print(f"\nEvaluating Claim #{idx}/{len(parsed_list)}: {p.get('subject')} - {p.get('metric')}")
            print(f"Parsed JSON:\n{json.dumps(p, indent=6)}")
            
            # Formulate a pseudo-claim string representing this sub-claim for validation prints
            sub_claim = f"{p.get('subject')} {p.get('metric')} = {p.get('claimed_value')}"
            
            v = validate_parsed_claim(p, sub_claim, skip_predictions=skip_predictions)
            v["claim"] = sub_claim
            verdicts.append(v)

        return {
            "is_multi_claim": True,
            "verdicts": verdicts,
            "status": "ok",
            "claim": claim_string
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Load .env if present
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    default_claim = "Babar Azam averages 50 in England against Spin"
    claim = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else default_claim
    validate_claim(claim)

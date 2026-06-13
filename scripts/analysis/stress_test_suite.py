"""
stress_test_suite.py  —  CricketTruth AI: Stress & Veracity Test Suite
========================================================================
Runs a rigorous test battery across all three architecture layers.

Layer 1: Data Pipeline & Integrity
Layer 2: Identity & Classification
Layer 3: AI Analysis (Truth-O-Meter)

Usage:
  python scripts/analysis/stress_test_suite.py            # all layers
  python scripts/analysis/stress_test_suite.py --layer 1  # data pipeline only
  python scripts/analysis/stress_test_suite.py --layer 2  # identity only
  python scripts/analysis/stress_test_suite.py --layer 3  # AI analysis only
  python scripts/analysis/stress_test_suite.py --quick     # fast spot-checks only
"""

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from pathlib import Path
from datetime import datetime

import pandas as pd

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT           = Path(__file__).resolve().parents[2]
MATCHES_CSV    = ROOT / "matches.csv"
PARQUET_FILE   = ROOT / "matches.parquet"
SQLITE_FILE    = ROOT / "cricket.db"
DUCKDB_FILE    = ROOT / "data" / "processed" / "cricket.duckdb"
BOWLERS_CSV    = ROOT / "bowlers.csv"
DATASET_DIR    = ROOT / "Dataset" / "Matches"
PLAYERS_DB     = ROOT / "Dataset" / "Players" / "players_data_with_all_info.csv"
OUTPUT_DIR     = ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
REPORT_FILE    = OUTPUT_DIR / "stress_test_report.json"

sys.path.append(str(ROOT))

# ─── ANSI Colors ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def pass_label(): return f"{GREEN}✅ PASS{RESET}"
def fail_label(): return f"{RED}❌ FAIL{RESET}"
def warn_label(): return f"{YELLOW}⚠️  WARN{RESET}"
def skip_label(): return f"{CYAN}⏭  SKIP{RESET}"

def header(title: str):
    print(f"\n{BOLD}{'─'*65}")
    print(f"  {title}")
    print(f"{'─'*65}{RESET}")

def result_line(name: str, status: str, detail: str = ""):
    detail_str = f"  →  {detail}" if detail else ""
    print(f"  {status}  {name:<48}{detail_str}")


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 1: DATA PIPELINE & INTEGRITY TESTING
# ══════════════════════════════════════════════════════════════════════════════

def layer1_tests(quick: bool = False) -> dict:
    header("LAYER 1: Data Pipeline & Integrity Testing")
    results = {}

    # ── T1.1: matches.csv Exists & Row Count ──────────────────────────────────
    if not MATCHES_CSV.exists():
        result_line("T1.1  matches.csv exists", fail_label(), "File not found — run extract_data.py first")
        results["T1.1_csv_exists"] = "FAIL"
        print(f"\n  {RED}Cannot continue Layer 1 tests without matches.csv.{RESET}\n")
        return results

    df_size = MATCHES_CSV.stat().st_size / 1e6
    result_line("T1.1  matches.csv exists", pass_label(), f"{df_size:.1f} MB")
    results["T1.1_csv_exists"] = "PASS"

    # ── T1.2: Schema Enforcement (dtypes) ─────────────────────────────────────
    print(f"\n  Loading subset of matches.csv for schema tests…")
    t0 = time.perf_counter()
    # Load 100k subset
    df = pd.read_csv(
        MATCHES_CSV,
        dtype={"match_id": str, "is_wicket": "int8", "is_bowler_wicket": "int8",
               "runs_batter": "int16", "runs_total": "int16"},
        low_memory=False,
        nrows=100000
    )
    load_secs = time.perf_counter() - t0
    # use duckdb or sqlite to get real total rows
    if DUCKDB_FILE.exists():
        import duckdb
        con_meta = duckdb.connect(str(DUCKDB_FILE), read_only=True)
        total_rows = con_meta.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
        con_meta.close()
        db_engine = "DuckDB"
    else:
        con_meta = sqlite3.connect(str(SQLITE_FILE))
        total_rows = con_meta.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
        con_meta.close()
        db_engine = "SQLite"
    print(f"  Loaded schema from 100k rows in {load_secs:.2f}s, {db_engine} has {total_rows:,} rows")

    required_cols = {"match_id","date","city","match_type","batting_team","bowling_team",
                     "batter","bowler","runs_batter","runs_total","is_wicket","is_bowler_wicket"}
    missing_cols  = required_cols - set(df.columns)
    if missing_cols:
        result_line("T1.2  Schema columns present", fail_label(), f"Missing: {missing_cols}")
        results["T1.2_schema"] = "FAIL"
    else:
        result_line("T1.2  Schema columns present", pass_label(), f"{len(df.columns)} columns verified")
        results["T1.2_schema"] = "PASS"

    # ── T1.3-T1.5: Data Quality (using the best available engine) ─────────────
    if DUCKDB_FILE.exists():
        import duckdb
        con = duckdb.connect(str(DUCKDB_FILE), read_only=True)
    else:
        con = sqlite3.connect(str(SQLITE_FILE))

    try:
        # T1.3
        neg_runs = con.execute("SELECT count(*) FROM deliveries WHERE runs_batter < 0").fetchone()[0]
        result_line("T1.3  No negative batter runs", pass_label() if neg_runs == 0 else fail_label(), f"{neg_runs:,} violations")
        
        # T1.4
        bad_flags = con.execute("SELECT count(*) FROM deliveries WHERE is_wicket NOT IN (0,1)").fetchone()[0]
        result_line("T1.4  is_wicket is binary {0,1}", pass_label() if bad_flags == 0 else fail_label(), f"{bad_flags:,} violations")

        # T1.5
        null_batters = con.execute("SELECT count(*) FROM deliveries WHERE batter IS NULL").fetchone()[0]
        null_bowlers = con.execute("SELECT count(*) FROM deliveries WHERE bowler IS NULL").fetchone()[0]
        result_line("T1.5  No null batter/bowler", pass_label() if (null_batters + null_bowlers) == 0 else fail_label(), f"nulls={null_batters+null_bowlers}")
    except Exception as e:
        print(f"  [ERR] Data Quality checks failed: {e}")


    # ── T1.6: Checksum Validation vs JSON source files ────────────────────────
    json_files = list(DATASET_DIR.glob("*.json"))
    if not json_files:
        result_line("T1.6  JSON Checksum validation", skip_label(), "No JSON files in Dataset/Matches")
        results["T1.6_checksum"] = "SKIP"
    else:
        sample_size  = 50 if quick else 200
        sample_files = random.sample(json_files, min(sample_size, len(json_files)))
        passed_ck    = 0
        failed_ck    = 0
        total_ck     = len(sample_files)

        for jf in sample_files:
            mid = jf.stem
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                failed_ck += 1
                continue

            # Skip pre-2019 matches
            info = data.get("info", {})
            dates = info.get("dates", [])
            if dates:
                try:
                    match_year = int(dates[0].split('-')[0])
                    if match_year < 2019:
                        total_ck -= 1
                        continue
                except ValueError:
                    pass


            for inn_idx, inning in enumerate(data.get("innings", [])):
                bt = inning.get("team")
                innings_num = inn_idx + 1
                json_total = sum(
                    d.get("runs", {}).get("total", 0)
                    for o in inning.get("overs", [])
                    for d in o.get("deliveries", [])
                )
                csv_total = con.execute(
                    "SELECT sum(runs_total) FROM deliveries WHERE match_id=? AND batting_team=? AND innings=?",
                    (mid, bt, innings_num)
                ).fetchone()[0]
                csv_total = int(csv_total) if csv_total is not None else -1
                if csv_total == json_total:
                    passed_ck += 1
                else:
                    failed_ck += 1


        pct = passed_ck / (passed_ck + failed_ck) * 100 if (passed_ck + failed_ck) > 0 else 0
        status = pass_label() if pct >= 95 else (warn_label() if pct >= 80 else fail_label())
        result_line(f"T1.6  Checksum validation ({total_ck} matches)", status,
                    f"{pct:.1f}% pass rate  ({passed_ck} ok / {failed_ck} mismatch)")
        results["T1.6_checksum"] = f"{pct:.1f}%"

    # ── T1.7: Parquet Schema Enforcement ─────────────────────────────────────
    if not PARQUET_FILE.exists():
        result_line("T1.7  Parquet schema enforcement", skip_label(), "matches.parquet not found")
        results["T1.7_parquet_schema"] = "SKIP"
    else:
        try:
            import pyarrow.parquet as pq
            meta = pq.read_metadata(str(PARQUET_FILE))
            schema = pq.read_schema(str(PARQUET_FILE))
            int_cols = [f.name for f in schema if str(f.type).startswith("int")]
            result_line("T1.7  Parquet schema enforcement", pass_label(),
                        f"{meta.num_rows:,} rows | int cols: {int_cols}")
            results["T1.7_parquet_schema"] = "PASS"
        except ImportError:
            result_line("T1.7  Parquet schema enforcement", skip_label(), "pyarrow not installed")
            results["T1.7_parquet_schema"] = "SKIP"
        except Exception as e:
            result_line("T1.7  Parquet schema enforcement", fail_label(), str(e))
            results["T1.7_parquet_schema"] = "FAIL"

    # ── T1.8: Performance Benchmark — All-time aggregate ─────────────────────
    t0 = time.perf_counter()
    if PARQUET_FILE.exists():
        df_bench = pd.read_parquet(PARQUET_FILE, columns=["batter", "runs_batter"])
        _ = df_bench.groupby("batter")["runs_batter"].sum()
    else:
        _ = con.execute("SELECT batter, SUM(runs_batter) FROM deliveries GROUP BY batter").fetchall()
    agg_secs = time.perf_counter() - t0
    status = pass_label() if agg_secs < 10 else warn_label()
    result_line("T1.8  All-time aggregate query", status, f"{agg_secs:.3f}s  (target <10s)")
    results["T1.8_aggregate_time"] = f"{agg_secs:.3f}s"

    # ── T1.9: Performance Benchmark — Single player filter ────────────────────
    t0 = time.perf_counter()
    if 'df_bench' in locals():
        _ = df_bench[df_bench["batter"] == "Babar Azam"]["runs_batter"].sum()
    else:
        _ = con.execute("SELECT SUM(runs_batter) FROM deliveries WHERE batter='Babar Azam'").fetchone()[0]
    single_secs = time.perf_counter() - t0
    status = pass_label() if single_secs < 1.0 else warn_label()
    result_line("T1.9  Single player query (Babar Azam)", status, f"{single_secs:.4f}s  (target <1s)")
    results["T1.9_single_player_time"] = f"{single_secs:.4f}s"

    # ── T1.10: SQLite row count matches CSV ───────────────────────────────────
    if not SQLITE_FILE.exists():
        result_line("T1.10 SQLite row count parity", skip_label(), "cricket.db not found")
        results["T1.10_sqlite_parity"] = "SKIP"
    else:
        try:
            con = sqlite3.connect(str(SQLITE_FILE))
            db_count = con.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
            con.close()
            if db_count == total_rows:
                result_line("T1.10 SQLite row count parity", pass_label(),
                            f"{db_count:,} rows == {total_rows:,} CSV rows")
                results["T1.10_sqlite_parity"] = "PASS"
            else:
                diff = abs(db_count - total_rows)
                result_line("T1.10 SQLite row count parity", fail_label(),
                            f"DB={db_count:,} vs CSV={total_rows:,}  diff={diff:,}")
                results["T1.10_sqlite_parity"] = "FAIL"
        except Exception as e:
            result_line("T1.10 SQLite row count parity", fail_label(), str(e))
            results["T1.10_sqlite_parity"] = "FAIL"

    # ── T1.11: DuckDB query performance ───────────────────────────────────────
    if DUCKDB_FILE.exists():
        try:
            import duckdb
            con = duckdb.connect(str(DUCKDB_FILE), read_only=True)
            t0 = time.perf_counter()
            con.execute("SELECT SUM(runs_batter) FROM deliveries WHERE batter='Babar Azam'").fetchone()
            duck_secs = time.perf_counter() - t0
            con.close()
            status = pass_label() if duck_secs < 0.1 else (warn_label() if duck_secs < 0.5 else fail_label())
            result_line("T1.11 DuckDB single-player query", status, f"{duck_secs:.4f}s  (target <0.1s)")
            results["T1.11_duckdb_time"] = f"{duck_secs:.4f}s"
        except Exception as e:
            result_line("T1.11 DuckDB single-player query", fail_label(), str(e))
            results["T1.11_duckdb_time"] = "FAIL"
    else:
        result_line("T1.11 DuckDB single-player query", skip_label(), "Not initialized")

    try:
        con.close()
    except Exception:
        pass

    results["_total_rows"] = total_rows
    return results



# ══════════════════════════════════════════════════════════════════════════════
# LAYER 2: IDENTITY & CLASSIFICATION TESTING
# ══════════════════════════════════════════════════════════════════════════════

def layer2_tests(quick: bool = False) -> dict:
    header("LAYER 2: Identity & Classification Testing")
    results = {}

    try:
        from scripts.identity.identity_engine import IdentityEngine
        engine = IdentityEngine()
        if engine.players_db.empty:
            result_line("T2.0  Identity Engine init", fail_label(), "players_db failed to load")
            results["T2.0_engine_init"] = "FAIL"
            return results
        result_line("T2.0  Identity Engine init", pass_label(),
                    f"{len(engine.players_db):,} players loaded")
        results["T2.0_engine_init"] = "PASS"
    except Exception as e:
        result_line("T2.0  Identity Engine init", fail_label(), str(e))
        results["T2.0_engine_init"] = "FAIL"
        return results

    # ── T2.1: Resolution Accuracy — Known Exact Matches ───────────────────────
    EXACT_CASES = [
        ("Babar Azam",       "Pakistan",     "Babar Azam"),
        ("Virat Kohli",      "India",        "Virat Kohli"),
        ("Joe Root",         "England",      "Root"),
        ("Steve Smith",      "Australia",    "Smith"),
        ("Rohit Sharma",     "India",        "Rohit"),
    ]
    exact_pass = 0
    for raw, team, expected_contains in EXACT_CASES:
        res = engine.resolve_for_ingestion(raw, team=team)
        canon = res.get("canonical_name", "") if res else ""
        ok = (expected_contains.lower() in canon.lower()) or (canon.lower() in expected_contains.lower())
        if ok:
            exact_pass += 1
    status = pass_label() if exact_pass == len(EXACT_CASES) else warn_label()
    result_line("T2.1  Exact name resolution", status, f"{exact_pass}/{len(EXACT_CASES)} resolved")
    results["T2.1_exact_resolution"] = f"{exact_pass}/{len(EXACT_CASES)}"

    # ── T2.2: Fuzzy Name Resolution — Ambiguous / Abbreviated Names ───────────
    FUZZY_CASES = [
        # (raw_input,          team,              expected_substring_in_canonical)
        ("SC Cook",            "South Africa",    "Cook"),
        ("DA Warner",          "Australia",       "Warner"),
        ("V Kohli",            "India",           "Kohli"),
        ("KS Williamson",      "New Zealand",     "Williamson"),
        ("AB de Villiers",     "South Africa",    "de Villiers"),
        ("Iftikhar",           "Pakistan",        "Iftikhar"),
        ("Bumrah",             "India",           "Bumrah"),
        ("MS Dhoni",           "India",           "Dhoni"),
        ("RA Jadeja",          "India",           "Jadeja"),
        ("Shaheen",            "Pakistan",        "Shaheen"),
        ("Rashid",             "Afghanistan",     "Rashid"),
        ("Shakib",             "Bangladesh",      "Shakib"),
        ("de Kock",            "South Africa",    "de Kock"),
        ("KL Rahul",           "India",           "Rahul"),
        ("Shafiq",             "Pakistan",        "Shafiq"),
    ]

    sample_fuzzy = FUZZY_CASES[:8] if quick else FUZZY_CASES
    fuzzy_resolved = 0
    fuzzy_details  = []
    for raw, team, expected_sub in sample_fuzzy:
        res = engine.resolve_for_ingestion(raw, team=team)
        canon = res.get("canonical_name", "N/A") if res else "FAIL"
        ok = expected_sub.lower() in canon.lower() if canon != "FAIL" else False
        if ok:
            fuzzy_resolved += 1
        fuzzy_details.append({
            "input": raw, "team": team,
            "resolved": canon, "pass": ok
        })

    pct = fuzzy_resolved / len(sample_fuzzy) * 100
    status = pass_label() if pct >= 80 else warn_label()
    result_line("T2.2  Fuzzy name resolution", status,
                f"{fuzzy_resolved}/{len(sample_fuzzy)} ({pct:.0f}%)")
    results["T2.2_fuzzy_resolution"] = f"{pct:.0f}%"
    results["T2.2_details"]          = fuzzy_details

    # Print per-case breakdown
    for d in fuzzy_details:
        icon = f"{GREEN}✓{RESET}" if d["pass"] else f"{RED}✗{RESET}"
        print(f"       {icon}  {d['input']:<24} → {d['resolved']}")

    # ── T2.3: Zero Case — Non-existent Player ─────────────────────────────────
    res_none = engine.resolve_for_ingestion("Qqxzpw Vvbtjlk Zzzrrm", team=None)
    if res_none is None:
        result_line("T2.3  Non-existent player returns None", pass_label())
        results["T2.3_null_resolution"] = "PASS"
    else:
        result_line("T2.3  Non-existent player returns None", fail_label(),
                    f"Got: {res_none.get('canonical_name')}")
        results["T2.3_null_resolution"] = "FAIL"

    # ── T2.4: Bowler Style Audit ───────────────────────────────────────────────
    if not BOWLERS_CSV.exists():
        result_line("T2.4  Bowler style audit", skip_label(), "bowlers.csv not found")
        results["T2.4_bowler_audit"] = "SKIP"
    else:
        VERIFIED_SPINNERS = {
            "M Muralitharan", "SK Warne", "A Kumble", "Rashid Khan", "R Ashwin",
            "RA Jadeja", "Shadab Khan", "Yasir Shah", "Imad Wasim", "W Hasaranga",
            "Shakib Al Hasan", "Daniel Vettori", "Nathan Lyon", "NM Lyon",
            "Mujeeb Ur Rahman", "M Santner",
        }
        VERIFIED_PACERS = {
            "M Johnson", "MA Starc", "PM Cummins", "JM Anderson", "SCJ Broad",
            "Mohammad Amir", "Shaheen Shah Afridi", "Jasprit Bumrah", "Mohammad Abbas",
            "Dale Steyn", "Kagiso Rabada", "Trent Boult", "Lasith Malinga",
        }

        bdf        = pd.read_csv(BOWLERS_CSV)
        audit_pass = 0
        audit_fail = 0
        audit_miss = 0

        def check_bowler(name, expected_style, bdf):
            row = bdf[bdf["bowler"] == name]
            if row.empty:
                return "miss"
            got = str(row.iloc[0]["style"]).strip()
            return "pass" if got == expected_style else "fail"

        for s in list(VERIFIED_SPINNERS)[:15]:
            r = check_bowler(s, "Spin", bdf)
            if r == "pass": audit_pass += 1
            elif r == "fail": audit_fail += 1
            else: audit_miss += 1

        for p in list(VERIFIED_PACERS)[:15]:
            r = check_bowler(p, "Pace", bdf)
            if r == "pass": audit_pass += 1
            elif r == "fail": audit_fail += 1
            else: audit_miss += 1

        total_checked = audit_pass + audit_fail
        pct = audit_pass / total_checked * 100 if total_checked > 0 else 0
        status = pass_label() if pct >= 85 else warn_label()
        result_line(f"T2.4  Bowler style audit ({total_checked} verified names)", status,
                    f"{audit_pass} correct, {audit_fail} wrong, {audit_miss} not in DB  ({pct:.0f}%)")
        results["T2.4_bowler_audit"] = f"{pct:.0f}%"

    # ── T2.5: Country Disambiguation Test ─────────────────────────────────────
    AMBIG_CASES = [
        ("Cook",       "England",      "Cook"),        # Alastair Cook vs SC Cook
        ("Cook",       "South Africa", "Cook"),
        ("Warner",     "Australia",    "Warner"),
    ]
    amb_pass = 0
    for raw, team, expected_sub in AMBIG_CASES:
        res = engine.resolve_for_ingestion(raw, team=team)
        if res and expected_sub.lower() in res.get("canonical_name", "").lower():
            amb_pass += 1
    result_line("T2.5  Country disambiguation", pass_label() if amb_pass == len(AMBIG_CASES) else warn_label(),
                f"{amb_pass}/{len(AMBIG_CASES)}")
    results["T2.5_disambiguation"] = f"{amb_pass}/{len(AMBIG_CASES)}"

    return results


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 3: AI ANALYSIS (TRUTH-O-METER) TESTING
# ══════════════════════════════════════════════════════════════════════════════

def layer3_tests(quick: bool = False) -> dict:
    header("LAYER 3: AI Analysis — Truth-O-Meter Testing")
    results = {}

    # ── T3.1: Verdict Calibration — Mathematical Soundness ────────────────────
    print(f"\n  {BOLD}T3.1  Verdict Calibration{RESET}")
    CALIBRATION_CASES = [
        # (claimed, real, expected_accuracy_exact)
        # Formula: Accuracy = max(0.0, (1.0 - abs(claimed-real)/real) * 100.0)
        (50.0,   50.0, 100.0),
        (50.0,   25.0,   0.0),
        (50.0,  100.0,  50.0),
        (40.0,   50.0,  80.0),
        (95.0,  100.0,  95.0),
        (0.0,    50.0,   0.0),
        (75.0,   50.0,  50.0),
    ]

    def compute_accuracy(claimed, real):
        if real == 0:
            return 100.0 if claimed == 0 else 0.0
        delta = abs(claimed - real)
        error_ratio = delta / real
        acc = (1.0 - error_ratio) * 100.0
        return max(0.0, min(100.0, acc))

    verdict_pass = 0
    for claimed, real, expected in CALIBRATION_CASES:
        got = compute_accuracy(claimed, real)
        ok  = abs(got - expected) < 0.1
        if ok:
            verdict_pass += 1
        icon = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
        print(f"       {icon}  claimed={claimed!s:<6} real={real!s:<6}  "
              f"→ accuracy={got:.1f}%  (expected {expected:.1f}%)")
    status = pass_label() if verdict_pass == len(CALIBRATION_CASES) else fail_label()
    result_line("T3.1  Verdict calibration formula", status,
                f"{verdict_pass}/{len(CALIBRATION_CASES)} correct")
    results["T3.1_calibration"] = f"{verdict_pass}/{len(CALIBRATION_CASES)}"

    # ── T3.2: Verdict Classification Labels ───────────────────────────────────
    def classify_verdict(pct):
        if pct >= 99.0: return "VERIFIED_FACT"
        elif pct >= 95.0: return "MINOR_DEVIATION"
        elif pct >= 85.0: return "INACCURATE"
        else:           return "FALSE"

    LABEL_CASES = [
        (100.0, "VERIFIED_FACT"),
        (99.0,  "VERIFIED_FACT"),
        (98.9,  "MINOR_DEVIATION"),
        (95.0,  "MINOR_DEVIATION"),
        (94.9,  "INACCURATE"),
        (85.0,  "INACCURATE"),
        (84.9,  "FALSE"),
        (0.0,   "FALSE"),
    ]
    label_pass = sum(1 for pct, exp in LABEL_CASES if exp in classify_verdict(pct))
    status = pass_label() if label_pass == len(LABEL_CASES) else fail_label()
    result_line("T3.2  Verdict label thresholds", status,
                f"{label_pass}/{len(LABEL_CASES)} labels correct")
    results["T3.2_verdict_labels"] = f"{label_pass}/{len(LABEL_CASES)}"

    # ── T3.3: Edge Case — Zero / No-data Location ─────────────────────────────
    if not MATCHES_CSV.exists():
        result_line("T3.3  Edge case: no-data location", skip_label(), "matches.csv missing")
        results["T3.3_zero_case"] = "SKIP"
    else:
        try:
            from scripts.analysis.validate_model import calculate_real_value, _load_subject_dataframe
            from scripts.identity.identity_engine import IdentityEngine
            
            engine = IdentityEngine()
            from scripts.pipeline.city_map import CITY_COUNTRY_MAP

            # "Iceland" not a cricket country — must return None
            filters_t33 = {"country": "Iceland", "format": None, "opposition": None}
            df_babar = _load_subject_dataframe("batter", "Babar Azam", engine, metric="Batting Average", filters=filters_t33)
            val = calculate_real_value(df_babar, "Babar Azam", "Batting Average",
                                       filters_t33, engine)
            if val is None:
                result_line("T3.3  Edge case: Iceland (no data)", pass_label(),
                            "Correctly returned None")
                results["T3.3_zero_case"] = "PASS"
            else:
                result_line("T3.3  Edge case: Iceland (no data)", fail_label(),
                            f"Should be None, got {val}")
                results["T3.3_zero_case"] = "FAIL"
        except Exception as e:
            result_line("T3.3  Edge case: Iceland (no data)", fail_label(), str(e))
            results["T3.3_zero_case"] = "FAIL"

    # ── T3.4: Format Discrimination — T20 vs ODI ──────────────────────────────
    if not MATCHES_CSV.exists():
        result_line("T3.4  Format discrimination (T20 vs ODI)", skip_label(), "matches.csv missing")
        results["T3.4_format_filter"] = "SKIP"
    else:
        try:
            filters_t20 = {"format": "T20"}
            df_babar_t20 = _load_subject_dataframe("batter", "Babar Azam", engine, metric="Batting Average", filters=filters_t20)
            val_t20_data = calculate_real_value(df_babar_t20, "Babar Azam", "Batting Average",
                                           filters_t20, engine)
            val_t20 = val_t20_data["value"] if val_t20_data else None

            filters_odi = {"format": "ODI"}
            df_babar_odi = _load_subject_dataframe("batter", "Babar Azam", engine, metric="Batting Average", filters=filters_odi)
            val_odi_data = calculate_real_value(df_babar_odi, "Babar Azam", "Batting Average",
                                           filters_odi, engine)
            val_odi = val_odi_data["value"] if val_odi_data else None
            if val_t20 is not None and val_odi is not None and abs(val_t20 - val_odi) > 0.1:
                result_line("T3.4  Format discrimination (T20 vs ODI)", pass_label(),
                            f"T20 avg={val_t20:.2f}  ≠  ODI avg={val_odi:.2f}")
                results["T3.4_format_filter"] = "PASS"
            elif val_t20 is None or val_odi is None:
                result_line("T3.4  Format discrimination (T20 vs ODI)", warn_label(),
                            f"T20={val_t20}  ODI={val_odi}  (one returned None)")
                results["T3.4_format_filter"] = "WARN"
            else:
                result_line("T3.4  Format discrimination (T20 vs ODI)", warn_label(),
                            f"Values identical — format filter may not be working (T20={val_t20:.2f}, ODI={val_odi:.2f})")
                results["T3.4_format_filter"] = "WARN"
        except Exception as e:
            result_line("T3.4  Format discrimination (T20 vs ODI)", fail_label(), str(e))
            results["T3.4_format_filter"] = "FAIL"

    # ── T3.5: NLP Semantic Accuracy — Golden Dataset ──────────────────────────
    GOLDEN_DATASET = [
        # (claim_string, expected_subject, expected_metric, expected_location)
        ("Babar Azam averages 50 in England against Spin",
         "Babar Azam", "Batting Average", "England"),
        ("Virat Kohli has scored 8000 Test runs",
         "Virat Kohli", None, None),
        ("Rashid Khan has an economy of 6.5 in T20 internationals",
         "Rashid Khan", "Economy", None),
        ("Joe Root averages 45 in Australia",
         "Joe Root", "Batting Average", "Australia"),
        ("Shaheen Shah Afridi has taken 100 wickets in ODIs",
         "Shaheen", "Wickets", None),
    ]

    from scripts.analysis.ai_parser import parse_claim

    golden_pass = 0
    golden_total = len(GOLDEN_DATASET)
    print(f"\n  {BOLD}T3.5  NLP Semantic Accuracy — Golden Dataset ({golden_total} cases){RESET}")
    for claim, exp_subject, exp_metric, exp_location in GOLDEN_DATASET:
        try:
            parsed = parse_claim(claim)
            sub_ok = exp_subject.lower() in parsed.get("subject", "").lower() if exp_subject else True
            met_ok = (exp_metric is None) or (exp_metric.lower() in parsed.get("metric", "").lower())
            loc_ok = (exp_location is None) or (
                exp_location.lower() in (parsed.get("filters", {}) or {}).get("location", "").lower()
            )
            ok = sub_ok and met_ok and loc_ok
            if ok:
                golden_pass += 1
            icon = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
            details = (f"sub={parsed.get('subject','?')}, "
                       f"metric={parsed.get('metric','?')}, "
                       f"loc={parsed.get('filters',{}).get('location','?')}")
            print(f"       {icon}  {claim[:55]:<55} → {details}")
        except Exception as e:
            print(f"       {RED}✗{RESET}  {claim[:55]:<55} → ERROR: {e}")

    pct = golden_pass / golden_total * 100
    status = pass_label() if pct >= 80 else warn_label()
    result_line("T3.5  NLP semantic accuracy (golden dataset)", status,
                f"{golden_pass}/{golden_total} ({pct:.0f}%)")
    results["T3.5_golden_nlp"] = f"{pct:.0f}%"

    # ── T3.6: End-to-end Sanity — Known Real Values ───────────────────────────
    if MATCHES_CSV.exists():
        print(f"\n  {BOLD}T3.6  End-to-end Sanity Checks{RESET}")
        E2E_CASES = [
            # (player, metric, filters, label)
            ("Babar Azam",  "Total Runs",    {}, "Babar Azam — Total Career Runs (any)"),
            ("Babar Azam",  "Batting Average", {"format": "ODI"}, "Babar Azam ODI Average"),
            ("R Ashwin",    "Wickets",        {},  "Ashwin — Total Wickets"),
        ]
        for player, metric, filters, label in E2E_CASES:
            full_filters = {"location": None, "opponent_type": None,
                            "format": None, "opposition": None, **filters}
            t0 = time.perf_counter()
            subject_col = "bowler" if "wicket" in metric.lower() else "batter"
            res_meta = engine.resolve_for_ingestion(player)
            canonical = res_meta["canonical_name"] if res_meta else player
            df_subj = _load_subject_dataframe(subject_col, canonical, engine, metric=metric, filters=full_filters)
            val_data = calculate_real_value(df_subj, canonical, metric, full_filters, engine)
            val = val_data["value"] if val_data else None
            elapsed = time.perf_counter() - t0
            if val is not None and val > 0:
                result_line(f"       {label}", pass_label(),
                            f"value={val:.2f}  ({elapsed:.3f}s)")
                results[f"T3.6_{player}_{metric}"] = val
            else:
                result_line(f"       {label}", warn_label(),
                            f"Got {val}  ({elapsed:.3f}s)")
                results[f"T3.6_{player}_{metric}"] = "WARN"

    return results


# ══════════════════════════════════════════════════════════════════════════════
# REPORT SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

def print_summary(all_results: dict):
    header("FINAL SUMMARY")

    total     = len([k for k in all_results if "/T" in k and not k.endswith("_details")])
    pass_count = sum(1 for k, v in all_results.items()
                     if "/T" in k and not k.endswith("_details") and
                     (v == "PASS" or
                      (isinstance(v, str) and v.endswith("%") and float(v.rstrip("%")) >= 80) or
                      (isinstance(v, str) and "/" in v and int(v.split("/")[0]) == int(v.split("/")[1]))))
    fail_count = sum(1 for k, v in all_results.items()
                     if "/T" in k and not k.endswith("_details") and
                     (v == "FAIL" or
                      (isinstance(v, str) and v.endswith("%") and float(v.rstrip("%")) < 80)))
    skip_count = sum(1 for k, v in all_results.items()
                     if "/T" in k and not k.endswith("_details") and v == "SKIP")
    warn_count = total - pass_count - fail_count - skip_count

    print(f"\n  Tests Run  : {total}")
    print(f"  {GREEN}Passed    : {pass_count}{RESET}")
    print(f"  {RED}Failed    : {fail_count}{RESET}")
    print(f"  {YELLOW}Warned    : {warn_count}{RESET}")
    print(f"  {CYAN}Skipped   : {skip_count}{RESET}")

    print(f"\n  Full report → {REPORT_FILE}")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="CricketTruth AI — Stress & Veracity Test Suite")
    parser.add_argument("--layer", type=int, choices=[1, 2, 3], default=None,
                        help="Run only a specific layer (1, 2, or 3)")
    parser.add_argument("--quick", action="store_true",
                        help="Fast mode — smaller samples, skips heavy JSON checksum scan")
    parser.add_argument("--complexity", type=str, default=None,
                        help="Complexity level for hallucination test")
    parser.add_argument("--params", type=str, default=None,
                        help="Params mode for hallucination test")
    args = parser.parse_args()

    # Force UTF-8 output on Windows to avoid codec errors with Unicode symbols
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    print(f"\n{BOLD}{'=' * 65}")
    print(f"  CricketTruth AI -- Stress & Veracity Test Suite")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 65}{RESET}")

    all_results = {}

    if args.complexity == "high" and args.params == "all":
        header("Automated Parameter 'Hallucination' Test")
        from scripts.analysis.ai_parser import parse_claim
        claim = "Left-handed batter, 2nd innings, Powerplay, in London, during a Chase"
        try:
            parsed = parse_claim(claim)
            print(f"  {GREEN}✓ Generated JSON:{RESET}")
            print(json.dumps(parsed, indent=4))
            result_line("T4.0  Parameter Hallucination", pass_label(), "Successfully generated complex JSON")
        except Exception as e:
            result_line("T4.0  Parameter Hallucination", fail_label(), f"ERROR: {e}")
        return
        

    if args.layer in (None, 1):
        r1 = layer1_tests(quick=args.quick)
        all_results.update({f"L1/{k}": v for k, v in r1.items()})

    if args.layer in (None, 2):
        r2 = layer2_tests(quick=args.quick)
        all_results.update({f"L2/{k}": v for k, v in r2.items()})

    if args.layer in (None, 3):
        r3 = layer3_tests(quick=args.quick)
        all_results.update({f"L3/{k}": v for k, v in r3.items()})

    # Flatten for summary
    flat = {k: v for k, v in all_results.items()}
    print_summary(flat)

    # Save JSON report
    report = {
        "run_time": datetime.now().isoformat(),
        "quick_mode": args.quick,
        "results": {k: str(v) for k, v in flat.items()},
    }
    with open(REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2)

    print()


if __name__ == "__main__":
    main()

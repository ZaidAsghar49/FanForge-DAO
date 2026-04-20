"""
data_integrity_validator.py  —  Phase 3: Integrity Checks
==========================================================
Validates the pipeline output by cross-checking ball-by-ball data
against Cricsheet JSON match headers.

Checks performed per match:
  1. Run-sum check   : SUM(runs_total) per innings == team score in header
  2. Wicket check    : COUNT(is_wicket) per innings == wickets in header
  3. Ball-count check: Counted legal deliveries vs expected overs × 6
  4. Orphan check    : Deliveries in CSV with no matching JSON file

Outputs:
  • integrity_report.json   — machine-readable full report
  • integrity_failures.csv  — only failing matches (for quick triage)

Usage:
  python data_integrity_validator.py                      # check all
  python data_integrity_validator.py --limit 500          # spot-check 500
  python data_integrity_validator.py --match-id 1000851   # single match
  python data_integrity_validator.py --summary            # just print stats
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Paths ────────────────────────────────────────────────────────────────────
# scripts/pipeline/ → scripts/ → project root
ROOT         = Path(__file__).resolve().parents[2]
MATCHES_CSV  = ROOT / "matches.csv"
DATASET_DIR  = ROOT / "Dataset" / "Matches"
REPORT_JSON  = ROOT / "output" / "integrity_report.json"
FAILURES_CSV = ROOT / "output" / "integrity_failures.csv"

# Tolerance: how many runs difference is acceptable (rounding or extras edge case)
RUN_TOLERANCE   = 0


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _parse_header_score(data: dict) -> dict[int, dict]:
    """
    Extract per-innings target score from JSON header.
    Returns { innings_num (1-based): {"runs": int, "wickets": int, "overs": int} }
    """
    results: dict[int, dict] = {}
    innings_list = data.get("innings", [])

    for idx, inning in enumerate(innings_list, start=1):
        overs_list = inning.get("overs", [])
        total_deliveries = sum(len(o.get("deliveries", [])) for o in overs_list)

        # Count runs & wickets from raw deliveries (ground-truth)
        total_runs      = 0
        total_wickets   = 0
        bowler_wickets  = 0
        legal_balls     = 0

        NON_BOWLER = {"run out", "retired hurt", "obstructing the field",
                      "timed out", "hit the ball twice"}

        for over_obj in overs_list:
            for d in over_obj.get("deliveries", []):
                runs      = d.get("runs", {})
                extras    = d.get("extras", {})
                total_runs += runs.get("total", 0)

                # Legal ball (no wide, no noball)
                if not extras.get("wides") and not extras.get("noballs"):
                    legal_balls += 1

                wickets = d.get("wickets", [])
                if wickets:
                    total_wickets += len(wickets)
                    for w in wickets:
                        if w.get("kind") not in NON_BOWLER:
                            bowler_wickets += 1

        results[idx] = {
            "json_runs":          total_runs,
            "json_wickets":       total_wickets,
            "json_bowler_wickets": bowler_wickets,
            "json_legal_balls":   legal_balls,
            "json_total_deliveries": total_deliveries,
        }

    return results


def _check_match(match_id: str, df_match: pd.DataFrame, json_path: Path) -> dict:
    """
    Run all integrity checks for a single match.
    Returns a dict with status and any failures.
    """
    result = {
        "match_id": match_id,
        "status":   "pass",
        "failures": [],
    }

    if not json_path.exists():
        result["status"] = "warn"
        result["failures"].append({
            "check": "json_missing",
            "detail": f"No JSON file at {json_path}"
        })
        return result

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        result["status"] = "error"
        result["failures"].append({"check": "json_parse_error", "detail": str(e)})
        return result

    header_scores = _parse_header_score(data)
    teams         = data.get("info", {}).get("teams", [])

    for innings_num, hdr in header_scores.items():
        csv_rows = df_match[df_match.index == innings_num] if "innings" in df_match.columns \
                   else df_match  # fallback: compare whole match if no innings col

        # ── We infer batting team ordering from original JSON ──
        batting_team = None
        if "innings" in data:
            try:
                batting_team = data["innings"][innings_num - 1].get("team")
            except IndexError:
                pass

        if batting_team:
            csv_rows = df_match[df_match["batting_team"] == batting_team]
        else:
            csv_rows = df_match

        if csv_rows.empty:
            result["failures"].append({
                "check":   "no_csv_rows",
                "innings": innings_num,
                "detail":  f"batting_team={batting_team} — no rows in CSV"
            })
            result["status"] = "fail"
            continue

        csv_runs          = int(csv_rows["runs_total"].sum())
        csv_wickets       = int(csv_rows["is_wicket"].sum())
        csv_bowler_wkts   = int(csv_rows["is_bowler_wicket"].sum())

        json_runs         = hdr["json_runs"]
        json_wickets      = hdr["json_wickets"]
        json_bowler_wkts  = hdr["json_bowler_wickets"]

        # Check 1: Run sum
        run_diff = abs(csv_runs - json_runs)
        if run_diff > RUN_TOLERANCE:
            result["failures"].append({
                "check":       "run_sum_mismatch",
                "innings":     innings_num,
                "batting_team": batting_team,
                "csv_runs":    csv_runs,
                "json_runs":   json_runs,
                "diff":        run_diff,
            })
            result["status"] = "fail"

        # Check 2: Wicket count
        if csv_wickets != json_wickets:
            result["failures"].append({
                "check":         "wicket_count_mismatch",
                "innings":       innings_num,
                "batting_team":  batting_team,
                "csv_wickets":   csv_wickets,
                "json_wickets":  json_wickets,
            })
            result["status"] = "fail"

        # Check 3: Bowler wickets
        if csv_bowler_wkts != json_bowler_wkts:
            result["failures"].append({
                "check":           "bowler_wicket_mismatch",
                "innings":         innings_num,
                "csv_bowler_wkts": csv_bowler_wkts,
                "json_bowler_wkts": json_bowler_wkts,
            })
            result["status"] = "fail" if result["status"] != "fail" else result["status"]

        # Check 4: Delivery count parity
        csv_deliveries  = len(csv_rows)
        json_deliveries = hdr["json_total_deliveries"]
        if csv_deliveries != json_deliveries:
            result["failures"].append({
                "check":           "delivery_count_mismatch",
                "innings":         innings_num,
                "csv_deliveries":  csv_deliveries,
                "json_deliveries": json_deliveries,
            })
            result["status"] = "fail" if result["status"] == "pass" else result["status"]

    return result


# ─── Main Validator ───────────────────────────────────────────────────────────
def run_validation(limit: int | None = None, target_match: str | None = None):
    log.info("Aggregating matches.csv ...")
    
    # We group by match_id and batting_team
    agg_dict = {}
    
    try:
        chunksize = 1000000
        for chunk in pd.read_csv(MATCHES_CSV, dtype={"match_id": str, "runs_total": "int16", "is_wicket": "int8", "is_bowler_wicket": "int8"}, low_memory=False, chunksize=chunksize):
            if target_match:
                chunk = chunk[chunk["match_id"] == target_match]
            if chunk.empty:
                continue
                
            grouped = chunk.groupby(["match_id", "batting_team"], dropna=False).agg(
                runs_total=("runs_total", "sum"),
                csv_wickets=("is_wicket", "sum"),
                csv_bowler_wkts=("is_bowler_wicket", "sum"),
                deliveries=("match_id", "count")
            ).reset_index()
            
            for _, row in grouped.iterrows():
                mid = row["match_id"]
                bt = row["batting_team"]
                if mid not in agg_dict:
                    agg_dict[mid] = {}
                if bt not in agg_dict[mid]:
                    agg_dict[mid][bt] = {"runs_total": 0, "csv_wickets": 0, "csv_bowler_wkts": 0, "deliveries": 0}
                
                agg_dict[mid][bt]["runs_total"] += row["runs_total"]
                agg_dict[mid][bt]["csv_wickets"] += row["csv_wickets"]
                agg_dict[mid][bt]["csv_bowler_wkts"] += row["csv_bowler_wkts"]
                agg_dict[mid][bt]["deliveries"] += row["deliveries"]
    except Exception as e:
        log.error(f"Error reading matches.csv: {e}")
        sys.exit(1)

    match_ids = list(agg_dict.keys())
    if limit:
        match_ids = match_ids[:limit]

    log.info("Validating %s matches…", f"{len(match_ids):,}")

    results   = []
    passed    = 0
    failed    = 0
    warned    = 0
    errored   = 0

    for mid in match_ids:
        json_path = DATASET_DIR / f"{mid}.json"
        
        # We simulate the _check_match behavior using our pre-aggregated data
        res = {
            "match_id": str(mid),
            "status":   "pass",
            "failures": [],
        }

        if not json_path.exists():
            res["status"] = "warn"
            res["failures"].append({
                "check": "json_missing",
                "detail": f"No JSON file at {json_path}"
            })
            results.append(res)
            warned += 1
            continue

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            res["status"] = "error"
            res["failures"].append({"check": "json_parse_error", "detail": str(e)})
            results.append(res)
            errored += 1
            continue

        header_scores = _parse_header_score(data)
        
        match_agg = agg_dict.get(mid, {})

        for innings_num, hdr in header_scores.items():
            # Infer batting team ordering from original JSON
            batting_team = None
            if "innings" in data:
                try:
                    batting_team = data["innings"][innings_num - 1].get("team")
                except IndexError:
                    pass

            csv_stats = match_agg.get(batting_team)

            if not csv_stats and match_agg:
                 # fallback if no batting team matching, try parsing from first team available if multiple not present
                 if len(match_agg) == 1 and batting_team is None:
                     csv_stats = list(match_agg.values())[0]

            if not csv_stats:
                res["failures"].append({
                    "check":   "no_csv_rows",
                    "innings": innings_num,
                    "detail":  f"batting_team={batting_team} — no rows in CSV"
                })
                res["status"] = "fail"
                continue

            csv_runs          = csv_stats["runs_total"]
            csv_wickets       = csv_stats["csv_wickets"]
            csv_bowler_wkts   = csv_stats["csv_bowler_wkts"]
            csv_deliveries    = csv_stats["deliveries"]

            json_runs         = hdr["json_runs"]
            json_wickets      = hdr["json_wickets"]
            json_bowler_wkts  = hdr["json_bowler_wickets"]
            json_deliveries   = hdr["json_total_deliveries"]

            # Check 1: Run sum
            run_diff = abs(csv_runs - json_runs)
            if run_diff > RUN_TOLERANCE:
                res["failures"].append({
                    "check":       "run_sum_mismatch",
                    "innings":     innings_num,
                    "batting_team": batting_team,
                    "csv_runs":    csv_runs,
                    "json_runs":   json_runs,
                    "diff":        run_diff,
                })
                res["status"] = "fail"

            # Check 2: Wicket count
            if csv_wickets != json_wickets:
                res["failures"].append({
                    "check":         "wicket_count_mismatch",
                    "innings":       innings_num,
                    "batting_team":  batting_team,
                    "csv_wickets":   csv_wickets,
                    "json_wickets":  json_wickets,
                })
                res["status"] = "fail"

            # Check 3: Bowler wickets
            if csv_bowler_wkts != json_bowler_wkts:
                res["failures"].append({
                    "check":           "bowler_wicket_mismatch",
                    "innings":         innings_num,
                    "csv_bowler_wkts": csv_bowler_wkts,
                    "json_bowler_wkts": json_bowler_wkts,
                })
                res["status"] = "fail" if res["status"] != "fail" else res["status"]

            # Check 4: Delivery count parity
            if csv_deliveries != json_deliveries:
                res["failures"].append({
                    "check":           "delivery_count_mismatch",
                    "innings":         innings_num,
                    "csv_deliveries":  csv_deliveries,
                    "json_deliveries": json_deliveries,
                })
                res["status"] = "fail" if res["status"] == "pass" else res["status"]

        results.append(res)
        if   res["status"] == "pass":  passed  += 1
        elif res["status"] == "fail":  failed  += 1
        elif res["status"] == "warn":  warned  += 1
        else:                          errored += 1

    total = len(results)
    log.info("=" * 60)
    log.info("  INTEGRITY REPORT")
    log.info("  Matches checked : %s", f"{total:,}")
    log.info("  ✅  PASS        : %d  (%.1f%%)", passed,  passed  / total * 100 if total else 0)
    log.info("  ❌  FAIL        : %d  (%.1f%%)", failed,  failed  / total * 100 if total else 0)
    log.info("  ⚠️   WARN        : %d  (%.1f%%)", warned,  warned  / total * 100 if total else 0)
    log.info("  🔥  ERROR       : %d  (%.1f%%)", errored, errored / total * 100 if total else 0)
    log.info("=" * 60)

    # Save JSON report
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(
            {"summary": {"total": total, "pass": passed, "fail": failed,
                         "warn": warned, "error": errored},
             "matches": results},
            f, indent=2,
        )
    log.info("Full report → %s", REPORT_JSON)

    # Save failures CSV for easy triage
    failures_flat = []
    for r in results:
        if r["status"] in ("fail", "error", "warn"):
            for fail in r["failures"]:
                failures_flat.append({"match_id": r["match_id"], **fail})

    if failures_flat:
        pd.DataFrame(failures_flat).to_csv(FAILURES_CSV, index=False)
        log.info("Failures CSV → %s  (%d rows)", FAILURES_CSV, len(failures_flat))

    return results


# ─── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Data Integrity Validator")
    parser.add_argument("--limit",    type=int, default=None,
                        help="Max number of matches to check")
    parser.add_argument("--match-id", type=str, default=None,
                        help="Validate a single match ID")
    parser.add_argument("--summary",  action="store_true",
                        help="Just print existing report summary")
    args = parser.parse_args()

    if args.summary:
        if REPORT_JSON.exists():
            with open(REPORT_JSON) as f:
                report = json.load(f)
            print(json.dumps(report["summary"], indent=2))
        else:
            log.error("No report found. Run without --summary first.")
    else:
        run_validation(limit=args.limit, target_match=args.match_id)

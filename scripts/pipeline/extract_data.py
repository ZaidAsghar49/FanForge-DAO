"""
extract_data.py — Full 38-Column Ball-by-Ball Re-Ingestion Engine
==================================================================
Processes ALL Cricsheet JSON files from Dataset/Matches/ and emits a single
matches.csv with every column required by the 38-parameter filter engine.

Output columns (38 + metadata):
  Match Context    : match_id, date, season, venue_name, city, country,
                     match_type, competition, day_night, neutral_venue,
                     toss_winner, toss_decision, team_a, team_b,
                     home_team, overs_limit
  Delivery Context : innings, over, ball, batting_team, bowling_team,
                     match_phase
  Batting          : batter, non_striker, batting_position,
                     runs_batter, is_wicket, wicket_type,
                     is_bowler_wicket
  Bowling          : bowler, bowler_type, bowler_hand,
                     runs_total, extras_wides, extras_noballs,
                     extras_byes, extras_legbyes

Usage:
    python scripts/pipeline/extract_data.py              # full run
    python scripts/pipeline/extract_data.py --limit 500  # test with 500 files
    python scripts/pipeline/extract_data.py --workers 4  # parallel (4 CPUs)
"""

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parents[2]
DATASET_DIR = str(ROOT / "Dataset" / "Matches")
OUTPUT_CSV  = str(ROOT / "matches.csv")
BOWLERS_CSV = str(ROOT / "bowlers.csv")
CITY_MAP_PY = str(ROOT / "scripts" / "pipeline" / "city_map.py")

# ── Load city → country map ───────────────────────────────────────────────────
sys.path.insert(0, str(ROOT))
from scripts.pipeline.city_map import CITY_COUNTRY_MAP

# ── Load bowler style cache from bowlers.csv ──────────────────────────────────
def _load_bowler_cache() -> dict[str, tuple[str, str]]:
    """Returns {bowler_name: (type, hand)} from bowlers.csv."""
    cache: dict[str, tuple[str, str]] = {}
    try:
        with open(BOWLERS_CSV, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                name  = row.get("bowler", "").strip()
                style = row.get("style", "").strip()
                btype = "Spin" if style == "Spin" else ("Pace" if style == "Pace" else "Unknown")
                cache[name] = (btype, "Unknown")   # hand requires players_db
    except Exception:
        pass
    return cache


BOWLER_CACHE: dict[str, tuple[str, str]] = {}   # filled in main() before workers


# ── Cricsheet field helpers ───────────────────────────────────────────────────

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
    """Pull competition / series name from event or competition keys."""
    event = info.get("event", {})
    if isinstance(event, dict):
        name = event.get("name", "")
    else:
        name = str(event)
    if name:
        return name
    return str(info.get("competition", "International")).strip() or "International"


def _home_team(info: dict) -> str:
    """
    Attempt to determine the home team.
    Cricsheet doesn't always record this explicitly; we use a heuristic:
      • team whose country matches the city country is 'home'
      • otherwise first team alphabetically (arbitrary but deterministic)
    """
    city    = info.get("city", "")
    country = CITY_COUNTRY_MAP.get(city, "")
    teams   = info.get("teams", [])
    if not teams:
        return ""
    # Map team → approximate country name
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
    return teams[0]   # fallback


BOWLER_WICKET_KINDS = frozenset({
    "bowled", "caught", "lbw", "stumped", "hit wicket", "caught and bowled"
})


# ── Per-file processor ────────────────────────────────────────────────────────

def process_file(filepath: str, bowler_cache: dict) -> list[dict]:
    """Parse one Cricsheet JSON and return a list of delivery dicts."""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []

    info     = data.get("info", {})
    innings  = data.get("innings", [])
    if not innings:
        return []

    # ── Match-level metadata (shared across all deliveries) ───────────────────
    match_id     = os.path.basename(filepath).split(".")[0]
    teams        = info.get("teams", [])
    dates        = info.get("dates", ["1970-01-01"])
    date_str     = dates[0] if dates else "1970-01-01"
    season       = str(info.get("season", date_str[:4]))
    venue_name   = info.get("venue", "")
    city         = info.get("city", venue_name)
    country      = CITY_COUNTRY_MAP.get(city, info.get("country", "Unknown"))
    match_type   = info.get("match_type", "Unknown")
    competition  = _competition(info)
    day_night    = _day_night(info)
    neutral_v    = bool(info.get("neutral_venue", False))
    toss         = info.get("toss", {})
    toss_winner  = toss.get("winner", "")
    toss_decision= toss.get("decision", "")
    overs_limit  = int(info.get("overs", 20 if match_type == "T20" else 50))
    team_a       = teams[0] if len(teams) > 0 else ""
    team_b       = teams[1] if len(teams) > 1 else ""
    home_team    = _home_team(info)

    records: list[dict] = []

    for inn_idx, inning in enumerate(innings):
        innings_num  = inn_idx + 1
        batting_team = inning.get("team", "")
        bowling_team = next((t for t in teams if t != batting_team), "")
        overs        = inning.get("overs", [])

        # ── Track batting order per innings ───────────────────────────────────
        batting_order: dict[str, int] = {}
        position_counter = 0

        for over_obj in overs:
            over_num = int(over_obj.get("over", 0))   # 0-indexed in Cricsheet
            phase    = _match_phase(over_num)
            deliveries = over_obj.get("deliveries", [])

            for ball_idx, delivery in enumerate(deliveries):
                batter      = delivery.get("batter", "")
                bowler      = delivery.get("bowler", "")
                non_striker = delivery.get("non_striker", "")

                # ── Batting position ──────────────────────────────────────────
                if batter and batter not in batting_order:
                    position_counter += 1
                    batting_order[batter] = position_counter
                bat_pos = batting_order.get(batter, 0)

                # ── Runs ──────────────────────────────────────────────────────
                runs_obj      = delivery.get("runs", {})
                runs_batter   = int(runs_obj.get("batter", 0))
                extras_val    = int(runs_obj.get("extras", 0))
                runs_total    = int(runs_obj.get("total", runs_batter + extras_val))

                # ── Extras breakdown ──────────────────────────────────────────
                extras_obj    = delivery.get("extras", {})
                ext_wides     = int(extras_obj.get("wides", 0))
                ext_noballs   = int(extras_obj.get("noballs", 0))
                ext_byes      = int(extras_obj.get("byes", 0))
                ext_legbyes   = int(extras_obj.get("legbyes", 0))

                # ── Wickets ───────────────────────────────────────────────────
                wicket_list      = delivery.get("wickets", [])
                is_wicket        = 0
                wicket_type      = ""
                is_bowler_wicket = 0

                if wicket_list:
                    w = wicket_list[0]
                    kind = w.get("kind", "")
                    # is_wicket == 1 when the batter is dismissed
                    if w.get("player_out", "") == batter:
                        is_wicket   = 1
                        wicket_type = kind
                    if kind in BOWLER_WICKET_KINDS:
                        is_bowler_wicket = 1

                # ── Bowler type & hand ────────────────────────────────────────
                btype, bhand = bowler_cache.get(bowler, ("Unknown", "Unknown"))

                records.append({
                    # ── Match context ─────────────────────────────────────
                    "match_id":      match_id,
                    "date":          date_str,
                    "season":        season,
                    "venue_name":    venue_name,
                    "city":          city,
                    "country":       country,
                    "match_type":    match_type,
                    "competition":   competition,
                    "day_night":     day_night,
                    "neutral_venue": neutral_v,
                    "toss_winner":   toss_winner,
                    "toss_decision": toss_decision,
                    "team_a":        team_a,
                    "team_b":        team_b,
                    "home_team":     home_team,
                    "overs_limit":   overs_limit,
                    # ── Delivery context ──────────────────────────────────
                    "innings":       innings_num,
                    "over":          over_num,
                    "ball":          ball_idx + 1,
                    "batting_team":  batting_team,
                    "bowling_team":  bowling_team,
                    "match_phase":   phase,
                    # ── Batting ───────────────────────────────────────────
                    "batter":          batter,
                    "non_striker":     non_striker,
                    "batting_position": bat_pos,
                    "runs_batter":     runs_batter,
                    "is_wicket":       is_wicket,
                    "wicket_type":     wicket_type,
                    "is_bowler_wicket": is_bowler_wicket,
                    # ── Bowling ───────────────────────────────────────────
                    "bowler":          bowler,
                    "bowler_type":     btype,
                    "bowler_hand":     bhand,
                    "runs_total":      runs_total,
                    "extras_wides":    ext_wides,
                    "extras_noballs":  ext_noballs,
                    "extras_byes":     ext_byes,
                    "extras_legbyes":  ext_legbyes,
                })

    return records


# ── Worker wrapper (needed for ProcessPoolExecutor) ───────────────────────────
def _worker(args):
    fp, cache = args
    return process_file(fp, cache)


# ── CSV column order ──────────────────────────────────────────────────────────
CSV_COLUMNS = [
    "match_id", "date", "season", "venue_name", "city", "country",
    "match_type", "competition", "day_night", "neutral_venue",
    "toss_winner", "toss_decision", "team_a", "team_b", "home_team", "overs_limit",
    "innings", "over", "ball", "batting_team", "bowling_team", "match_phase",
    "batter", "non_striker", "batting_position",
    "runs_batter", "is_wicket", "wicket_type", "is_bowler_wicket",
    "bowler", "bowler_type", "bowler_hand",
    "runs_total", "extras_wides", "extras_noballs", "extras_byes", "extras_legbyes",
]


# ── Main ──────────────────────────────────────────────────────────────────────

def main(limit: int | None = None, workers: int = 1,
         batch_size: int = 500, output: str = OUTPUT_CSV) -> None:
    t0 = time.time()

    # Load bowler cache before scanning files (fast)
    global BOWLER_CACHE
    BOWLER_CACHE = _load_bowler_cache()
    print(f"[extract_data] Bowler cache loaded: {len(BOWLER_CACHE):,} entries.")

    # Scan JSON files
    import glob as _glob
    all_files = sorted(_glob.glob(os.path.join(DATASET_DIR, "*.json")))
    if limit:
        all_files = all_files[:limit]
    total_files = len(all_files)
    print(f"[extract_data] Found {total_files:,} JSON files to process.")
    print(f"[extract_data] Output → {output}")
    print(f"[extract_data] Workers: {workers}  |  Batch size: {batch_size:,}")

    first_write = True
    total_rows  = 0
    processed   = 0
    errors      = 0

    def _flush(batch_records: list[dict]) -> None:
        nonlocal first_write, total_rows
        if not batch_records:
            return
        import csv as _csv
        mode   = "w" if first_write else "a"
        header = first_write
        with open(output, mode, newline="", encoding="utf-8") as fh:
            writer = _csv.DictWriter(fh, fieldnames=CSV_COLUMNS,
                                     extrasaction="ignore")
            if header:
                writer.writeheader()
            writer.writerows(batch_records)
        total_rows  += len(batch_records)
        first_write  = False

    pending_records: list[dict] = []

    if workers > 1:
        # ── Parallel processing ───────────────────────────────────────────────
        args_list = [(fp, BOWLER_CACHE) for fp in all_files]
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_worker, a): a[0] for a in args_list}
            for fut in as_completed(futures):
                processed += 1
                try:
                    recs = fut.result()
                    pending_records.extend(recs)
                except Exception as exc:
                    errors += 1
                    print(f"  [ERR] {futures[fut]}: {exc}")

                if len(pending_records) >= batch_size * 400:
                    _flush(pending_records)
                    pending_records.clear()
                    eta = (time.time() - t0) / processed * (total_files - processed)
                    print(f"  [{processed:>6}/{total_files}]  rows={total_rows:,}  "
                          f"errors={errors}  ETA={eta:.0f}s")
    else:
        # ── Sequential processing ─────────────────────────────────────────────
        for i, fp in enumerate(all_files):
            try:
                recs = process_file(fp, BOWLER_CACHE)
                pending_records.extend(recs)
            except Exception as exc:
                errors += 1
                print(f"  [ERR] {os.path.basename(fp)}: {exc}")

            processed += 1
            if processed % batch_size == 0 or processed == total_files:
                _flush(pending_records)
                pending_records.clear()
                elapsed = time.time() - t0
                pct     = processed / total_files * 100
                rate    = processed / elapsed if elapsed > 0 else 0
                eta     = (total_files - processed) / rate if rate > 0 else 0
                print(f"  [{processed:>6}/{total_files}]  {pct:5.1f}%  "
                      f"rows={total_rows:,}  errors={errors}  "
                      f"elapsed={elapsed:.0f}s  ETA={eta:.0f}s")

    # Flush any remaining
    _flush(pending_records)

    elapsed = time.time() - t0
    print(f"\n[extract_data] ✅ DONE")
    print(f"  Files processed : {processed:,} / {total_files:,}  (errors: {errors})")
    print(f"  Total rows      : {total_rows:,}")
    print(f"  Output file     : {output}")
    print(f"  Time elapsed    : {elapsed:.1f}s  ({elapsed/60:.1f} min)")
    size_mb = os.path.getsize(output) / 1e6 if os.path.exists(output) else 0
    print(f"  File size       : {size_mb:.1f} MB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Re-ingest Cricsheet JSON → matches.csv (38 columns)"
    )
    parser.add_argument("--limit",   type=int, default=None,
                        help="Process only first N files (for testing)")
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of parallel worker processes (default: 1)")
    parser.add_argument("--batch",   type=int, default=500,
                        help="Flush to CSV every N files (default: 500)")
    parser.add_argument("--output",  type=str, default=OUTPUT_CSV,
                        help="Output CSV path (default: matches.csv in project root)")
    args = parser.parse_args()
    main(limit=args.limit, workers=args.workers,
         batch_size=args.batch, output=args.output)

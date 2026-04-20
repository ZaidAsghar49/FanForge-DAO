import json
import os
import time
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)

    from scripts.update_cricsheet_data import fetch_cricsheet_data
    from pipeline.match_parser import process_new_matches

    print("[1/3] Updating Cricsheet dataset...")
    fetch_cricsheet_data()

    raw_dir = root / "data" / "raw" / "cricsheet"
    processed_dir = root / "data" / "processed"
    db_path = processed_dir / "cricket.duckdb"
    log_file = processed_dir / "ingested_matches.json"

    # Fresh rebuild by default: delete old DB and reset log
    print("[2/3] Resetting DuckDB + ingestion log...")
    if db_path.exists():
        db_path.unlink()
    processed_dir.mkdir(parents=True, exist_ok=True)
    log_file.write_text("[]", encoding="utf-8")

    total_raw = len(list(raw_dir.glob("*.json")))
    print(f"    Raw JSON files available: {total_raw}")

    print("[3/3] Ingesting into DuckDB (progress in output/duckdb_ingestion_status.json)...")
    t0 = time.time()
    matches = process_new_matches(str(raw_dir), str(db_path), str(log_file))
    dt = time.time() - t0

    print(f"[DONE] matches_ingested={matches} time_minutes={dt/60:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


import sys
import os
from pathlib import Path
import logging

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("pipeline.log"),
        logging.StreamHandler()
    ]
)

# Root Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.update_cricsheet_data import fetch_cricsheet_data
from pipeline.match_parser import process_new_matches
from pipeline.feature_builder import build_global_features
from pipeline.retrain_models import retrain_models

def run_pipeline():
    logging.info("=== Starting Daily AI Analytics Pipeline (DuckDB Mode) ===")
    
    try:
        # Step 1: Fetch Data
        logging.info("Step 1: Fetching new data from Cricsheet...")
        fetch_cricsheet_data()
        
        # Step 2: Parse and Ingest
        logging.info("Step 2: Parsing new matches into cricket.duckdb...")
        new_matches = process_new_matches(
            raw_dir="data/raw/cricsheet",
            db_path="data/processed/cricket.duckdb",
            log_file="data/processed/ingested_matches.json"
        )
        
        # Step 3: Feature Engineering
        if new_matches > 0 or not Path("data/features/batting_features.parquet").exists():
            logging.info("Step 3: Building new features...")
            build_global_features()
        else:
            logging.info("Step 3: Skipping feature building (no new data).")
            
        # Step 4: Retrain Models
        if new_matches > 50 or not Path("models/model_registry.json").exists(): # Threshold for retraining
            logging.info("Step 4: Retraining models...")
            retrain_models()
        else:
            logging.info("Step 4: Skipping model retraining (not enough new data).")
            
        logging.info("=== Pipeline Completed Successfully ===")
        
    except Exception as e:
        logging.error(f"Pipeline failed: {e}", exc_info=True)

if __name__ == "__main__":
    run_pipeline()

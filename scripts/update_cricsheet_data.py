import os
import requests
import zipfile
import json
from pathlib import Path

# Config
CRICSHEET_URL = "https://cricsheet.org/downloads/all_json.zip"
RAW_DIR = Path("data/raw/cricsheet")
LOG_FILE = Path("data/processed/processed_matches.json")

def fetch_cricsheet_data():
    """Downloads and extracts the latest ball-by-ball data from Cricsheet."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    
    zip_path = RAW_DIR / "all_json.zip"
    
    print(f"[*] Fetching latest data from {CRICSHEET_URL}...")
    response = requests.get(CRICSHEET_URL, stream=True)
    if response.status_code == 200:
        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print("[+] Download complete.")
    else:
        print(f"[-] Failed to download data. Status code: {response.status_code}")
        return

    print("[*] Extracting matches...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        # We only extract .json files
        json_files = [f for f in zip_ref.namelist() if f.endswith('.json') and f != 'README.txt']
        
        print(f"[*] Total files in ZIP: {len(json_files)}")
        existing = {p.name for p in RAW_DIR.glob("*.json")}
        new_files = [f for f in json_files if Path(f).name not in existing]
        print(f"[*] Already present locally: {len(existing)}")
        print(f"[*] New files to extract: {len(new_files)}")
        
        for file in new_files:
            zip_ref.extract(file, RAW_DIR)
            
    print(f"[+] Extraction complete. {len(new_files)} new matches added to {RAW_DIR}")
    
    # Cleanup zip
    os.remove(zip_path)

if __name__ == "__main__":
    fetch_cricsheet_data()

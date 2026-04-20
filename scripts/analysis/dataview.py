import pandas as pd
from pathlib import Path

# Resolve the absolute path to the directory where THIS script is located
# scripts/analysis/dataview.py -> parents[0] is analysis/, parents[1] is scripts/
# We need parents[2] to reach the FYP/AI/ root
root = Path(__file__).resolve().parents[2] 
csv_path = root / "matches.csv"

print(f"Searching for file at: {csv_path}")

if csv_path.exists():
    df_head = pd.read_csv(csv_path, nrows=5)
    print("--- Success! First 5 Rows ---")
    print(df_head)
else:
    print(f"❌ Error: Could not find matches.csv at {csv_path}")
    # List files in root to help debug
    print(f"Files found in root: {[f.name for f in root.iterdir()]}")
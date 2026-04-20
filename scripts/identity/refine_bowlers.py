import pandas as pd
import os
from pathlib import Path

ROOT         = Path(__file__).resolve().parents[2]
BOWLERS_FILE = str(ROOT / 'bowlers.csv')
REF_FILE     = str(ROOT / 'Dataset' / 'Players' / 'players_data_with_all_info.csv')
OUTPUT_FILE  = str(ROOT / 'bowlers.csv')

def load_extended_reference():
    """
    Loads 'Dataset/Players/players_data_with_all_info.csv' and builds a map:
    { "clean_name": "Spin" | "Pace" }
    """
    if not os.path.exists(REF_FILE):
        print("Extended reference file not found!")
        return {}
        
    print(f"Loading extended reference: {REF_FILE}...")
    try:
        df = pd.read_csv(REF_FILE)
        ref_map = {}
        
        spin_keywords = ['legbreak', 'offbreak', 'slow', 'spin', 'orthodox', 'chinaman', 'googl']
        pace_keywords = ['fast', 'medium', 'seam']
        
        count = 0
        for _, row in df.iterrows():
            name = str(row.get('fullname', '')).strip()
            style_str = str(row.get('bowlingstyle', '')).lower()
            
            if not name or name == 'nan': continue
            if not style_str or style_str == 'nan': continue
            
            is_spin = any(k in style_str for k in spin_keywords)
            is_pace = any(k in style_str for k in pace_keywords)
            
            final_style = None
            if is_spin: final_style = 'Spin'
            elif is_pace: final_style = 'Pace'
            
            if final_style:
                ref_map[name.lower()] = final_style
                parts = name.split()
                if len(parts) >= 2:
                    v1 = f"{parts[0][0]} {parts[-1]}".lower()
                    ref_map[v1] = final_style
        
        print(f"Loaded {len(ref_map)} reference signatures.")
        return ref_map
        
    except Exception as e:
        print(f"Error loading reference: {e}")
        return {}

def main():
    if not os.path.exists(BOWLERS_FILE):
        print("No existing bowlers.csv to refine.")
        return

    print("Loading current bowlers DB...")
    df = pd.read_csv(BOWLERS_FILE)
    print(f"Current rows: {len(df)}")
    
    ref_map = load_extended_reference()
    if not ref_map:
        return

    updates = 0
    
    for index, row in df.iterrows():
        b_name = str(row['bowler'])
        current_style = row['style']
        
        b_clean = b_name.lower().strip()
        
        new_style = None
        
        # Check Reference
        if b_clean in ref_map:
            new_style = ref_map[b_clean]
        
        # Update if different
        if new_style and new_style != current_style:
            # print(f"Refining {b_name}: {current_style} -> {new_style}")
            df.at[index, 'style'] = new_style
            updates += 1
            
    print(f"Refinement Complete. Updated {updates} bowlers based on official dataset.")
    
    df.to_csv(OUTPUT_FILE, index=False)
    print("Database Saved.")

if __name__ == "__main__":
    main()

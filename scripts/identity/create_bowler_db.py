import pandas as pd
import os
import sys
import google.generativeai as genai
import cohere
from dotenv import load_dotenv
import time
import json

load_dotenv()

from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

# Configure APIs
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
COHERE_API_KEY = os.getenv('COHERE_API_KEY')

MATCHES_FILE = str(ROOT / 'matches.csv')
OUTPUT_FILE  = str(ROOT / 'bowlers.csv')

# Expanded Manual List (Knowledge Base)
KNOWN_SPINNERS = {
    # India
    "R Ashwin", "RA Jadeja", "Harbhajan Singh", "A Kumble", "BS Bedi", "EAS Prasanna", "BS Chandrasekhar",
    "S Venkataraghavan", "Kuldeep Yadav", "YS Chahal", "Axar Patel", "Washington Sundar", "R Bishnoi",
    "V Sehwag", "SR Tendulkar", "Yuvraj Singh", "SK Raina", 
    # Australia
    "SK Warne", "NM Lyon", "SCG MacGill", "Brad Hogg", "A Zampa", "TM Head", "GJ Maxwell", "AC Agar",
    "M Labuschagne", "SPD Smith",
    # England
    "Graeme Swann", "Moeen Ali", "Adil Rashid", "Jack Leach", "Monty Panesar", "Derek Underwood",
    "JE Root",
    # Pakistan
    "Saeed Ajmal", "Shahid Afridi", "Saqlain Mushtaq", "Mushtaq Ahmed", "Yasir Shah", "Shadab Khan",
    "Imad Wasim", "Mohammad Hafeez", "Shoaib Malik", "Danish Kaneria", "Abdul Qadir",
    # Sri Lanka
    "M Muralitharan", "HMRKB Herath", "W Hasaranga", "M Theekshana", "A Mendis", "ST Jayasuriya", 
    "TM Dilshan",
    # South Africa
    "Imran Tahir", "Keshav Maharaj", "Tabraiz Shamsi", "Paul Adams", "JP Duminy",
    # West Indies
    "Sunil Narine", "S Badree", "Lance Gibbs", "Carl Hooper", "CH Gayle", "MN Samuels",
    # New Zealand
    "Daniel Vettori", "M Santner", "Ish Sodhi", "DN Patel", "J Patel",
    # Afghanistan
    "Rashid Khan", "Mujeeb Ur Rahman", "Mohammad Nabi", "Noor Ahmad",
    # Bangladesh
    "Shakib Al Hasan", "Mehidy Hasan Miraz", "Taijul Islam", "Abdur Razzak",
    # Zimbabwe
    "Ray Price", "Graeme Cremer", "Sikandar Raza"
}

def classify_with_ai_retry(bowlers_list, retries=3):
    """
    Uses AI to classify a batch of bowlers with retry logic.
    """
    if not bowlers_list:
        return {}
        
    prompt = f"""
    Classify the following cricket bowlers as 'Spin' or 'Pace'.
    If uncertain or part-timer who bowls spin, mark as 'Spin'.
    If unknown, mark as 'Pace' (default).
    
    List: {", ".join(bowlers_list)}
    
    Output JSON format only:
    {{
        "Bowler Name": "Spin" | "Pace",
        ...
    }}
    """
    
    for attempt in range(retries):
        try:
            response_text = ""
            
            # Try Gemini first
            if GEMINI_API_KEY:
                try:
                    genai.configure(api_key=GEMINI_API_KEY)
                    model = genai.GenerativeModel('gemini-pro')
                    resp = model.generate_content(prompt)
                    response_text = resp.text
                except Exception as e:
                    print(f"Gemini Error (Attempt {attempt+1}): {e}")
            
            # Try Cohere if Gemini failed/missing
            if not response_text and COHERE_API_KEY:
                try:
                    co = cohere.Client(COHERE_API_KEY)
                    resp = co.chat(message=prompt, model="command-r-08-2024", temperature=0)
                    response_text = resp.text
                except Exception as e:
                    print(f"Cohere Error (Attempt {attempt+1}): {e}")
            
            if response_text:
                # Clean and Parse
                json_str = response_text
                if "```json" in response_text:
                    json_str = response_text.split("```json")[1].split("```")[0]
                elif "```" in response_text:
                     json_str = response_text.split("```")[1].split("```")[0]
                
                return json.loads(json_str.strip())
                
        except Exception as e:
            print(f"Parsing/Other Error (Attempt {attempt+1}): {e}")
            time.sleep(2 * (attempt + 1)) # Backoff
            
    return {}

def load_extended_reference():
    """
    Loads 'Dataset/Players/players_data_with_all_info.csv' and builds a map:
    { "clean_name": "Spin" | "Pace" }
    """
    ref_file = str(ROOT / "Dataset" / "Players" / "players_data_with_all_info.csv")
    if not os.path.exists(ref_file):
        print("Extended reference file not found!")
        return {}
        
    print(f"Loading extended reference: {ref_file}...")
    try:
        df = pd.read_csv(ref_file)
        # Required cols: fullname, bowlingstyle
        # Check naming convention
        
        ref_map = {}
        
        spin_keywords = ['legbreak', 'offbreak', 'slow', 'spin', 'orthodox', 'chinaman', 'googl']
        pace_keywords = ['fast', 'medium', 'seam']
        
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
                # 1. Full Match
                ref_map[name.lower()] = final_style
                
                # 2. Initials + Last Name (e.g. "Ahmed Shehzad" -> "A Shehzad")
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
    if not os.path.exists(MATCHES_FILE):
        print("Matches file not found. Run extract_data.py first.")
        return

    print("Reading matches dataset...")
    df = pd.read_csv(MATCHES_FILE)
    
    # 1. Get All Target Bowlers
    bowler_counts = df['bowler'].value_counts()
    all_bowlers = bowler_counts.index.tolist()
    print(f"Total Unique Bowlers in Matches: {len(all_bowlers)}")
    
    # 2. Load Existing Progress
    processed_bowlers = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            existing_df = pd.read_csv(OUTPUT_FILE)
            if 'bowler' in existing_df.columns:
                processed_bowlers = set(existing_df['bowler'].unique())
            print(f"Found {len(processed_bowlers)} already classified bowlers.")
        except Exception as e:
            print(f"Error reading existing file: {e}. Starting fresh.")
    else:
        # Create file with header
        pd.DataFrame(columns=['bowler', 'style']).to_csv(OUTPUT_FILE, index=False)

    # Load Extended Reference
    ref_map = load_extended_reference()

    # 3. Filter Missing
    missing_bowlers = [b for b in all_bowlers if b not in processed_bowlers]
    print(f"Bowlers remaining to classify: {len(missing_bowlers)}")
    
    if not missing_bowlers:
        print("All bowlers classified!")
        return

    # 4. Processing Loop
    manual_batch = []
    ai_batch = []
    
    BATCH_SIZE = 40 
    
    for bowler in missing_bowlers:
        # A. Check Extended Reference First
        b_clean = bowler.lower().strip()
        
        # Exact Lower Match
        if b_clean in ref_map:
             manual_batch.append({'bowler': bowler, 'style': ref_map[b_clean]})
             continue
             
        # B. Manual Heuristics (Known List)
        is_known_spin = False
        parts = set(bowler.lower().split())
        
        # Check against Knowledge Base
        for k in KNOWN_SPINNERS:
            if k == bowler:
                is_known_spin = True
                break
            # Last Name + First char match heuristic
            k_parts = k.split()
            b_parts = bowler.split()
            if len(k_parts) > 1 and len(b_parts) > 1:
                if k_parts[-1].lower() == b_parts[-1].lower() and k_parts[0][0].lower() == b_parts[0][0].lower():
                     is_known_spin = True
                     break
        
        if is_known_spin:
            manual_batch.append({'bowler': bowler, 'style': 'Spin'})
        else:
            # C. Decision: AI or Default?
            if bowler_counts[bowler] > 50:
                ai_batch.append(bowler)
            else:
                manual_batch.append({'bowler': bowler, 'style': 'Pace'})
        
        # Flush Manual Batch
        if len(manual_batch) >= 100:
            pd.DataFrame(manual_batch).to_csv(OUTPUT_FILE, mode='a', header=False, index=False)
            print(f"Saved {len(manual_batch)} heuristic/ref results.")
            manual_batch = []
            
        # Flush AI Batch
        if len(ai_batch) >= BATCH_SIZE:
            print(f"Processing AI Batch ({len(ai_batch)} bowlers)...")
            ai_results = classify_with_ai_retry(ai_batch)
            
            # Map results to list
            to_save = []
            for b_name in ai_batch:
                style = ai_results.get(b_name, 'Pace') 
                if 'spin' in str(style).lower():
                    to_save.append({'bowler': b_name, 'style': 'Spin'})
                else:
                    to_save.append({'bowler': b_name, 'style': 'Pace'})
            
            pd.DataFrame(to_save).to_csv(OUTPUT_FILE, mode='a', header=False, index=False)
            print(f"Saved {len(to_save)} AI results.")
            ai_batch = []
            time.sleep(2) 
            
    # Final Flush
    if manual_batch:
        pd.DataFrame(manual_batch).to_csv(OUTPUT_FILE, mode='a', header=False, index=False)
    
    if ai_batch:
        print(f"Processing Final AI Batch ({len(ai_batch)} bowlers)...")
        ai_results = classify_with_ai_retry(ai_batch)
        to_save = []
        for b_name in ai_batch:
            style = ai_results.get(b_name, 'Pace')
            if 'spin' in str(style).lower():
                to_save.append({'bowler': b_name, 'style': 'Spin'})
            else:
                to_save.append({'bowler': b_name, 'style': 'Pace'})
        pd.DataFrame(to_save).to_csv(OUTPUT_FILE, mode='a', header=False, index=False)

    print("Classification Complete.")

if __name__ == "__main__":
    main()

import os
import sys
import json
import time
from pathlib import Path
from dotenv import load_dotenv

# Load env variables to ensure API keys are present
env_path = Path(__file__).resolve().parents[2] / '.env'
load_dotenv(dotenv_path=env_path)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.analysis.ai_parser import parse_claim

# 38 isolated tests for each parameter
TESTS = [
    # --- MATCH CONTEXT ---
    ("Babar Azam scored 100 at Melbourne Cricket Ground", "venue_name", "Melbourne Cricket Ground", "filters"),
    ("Virat Kohli average in Melbourne", "city", "Melbourne", "filters"),
    ("Rohit Sharma centuries in Australia", "country", "Australia", "filters"),
    ("Steve Smith runs in Test matches", "format", "Test", "filters"),
    ("Joe Root average in 2022 season", "season", "2022", "filters"),
    ("Kane Williamson performance in Day/Night matches", "day_night", "Day/Night", "filters"),
    ("David Warner score when Australia won the toss", "toss_winner", "Australia", "filters"),
    ("Marnus Labuschagne average when toss decision is bat", "toss_decision", "bat", "filters"),
    ("Babar Azam average in the 1st innings", "innings", 1, "filters"),
    ("Virat Kohli average in the Ashes", "series", "Ashes", "filters"),
    ("Rohit Sharma runs Away from home", "home_away", "Away", "filters"),
    ("Jasprit Bumrah wickets in neutral venues", "neutral_venue", True, "filters"),

    # --- SUBJECT ---
    ("Total Runs of Babar Azam", "subject", "Babar Azam", "root"),

    # --- BATTING METRICS ---
    ("Total Runs of Babar Azam", "metric", "Total Runs", "root"),
    ("Balls Faced by Virat Kohli", "metric", "Balls Faced", "root"),
    ("Batting Average of Rohit Sharma", "metric", "Batting Average", "root"),
    ("Strike Rate of Steve Smith", "metric", "Strike Rate", "root"),
    ("Dot Ball % of Kane Williamson", "metric", "Dot Ball %", "root"),
    ("Boundary % of David Warner", "metric", "Boundary %", "root"),
    ("Partnership Runs of Virat Kohli", "metric", "Partnership Runs", "root"),
    ("High Score of Rohit Sharma", "metric", "High Score", "root"),
    ("Milestones of Steve Smith", "metric", "Milestones", "root"),

    # --- BATTING FILTERS ---
    ("Joe Root bowled out", "dismissal_type", "bowled", "filters"),
    ("Marnus Labuschagne average at batting position 3", "batting_position", 3, "filters"),
    ("Babar Azam with Mohammad Rizwan as non striker", "non_striker", "Mohammad Rizwan", "filters"),

    # --- BOWLING METRICS ---
    ("Economy Rate of Jasprit Bumrah", "metric", "Economy Rate", "root"),
    ("Bowling Strike Rate of Pat Cummins", "metric", "Bowling Strike Rate", "root"),
    ("Wickets by Shaheen Afridi", "metric", "Wickets", "root"),
    ("Dots Forced by Rashid Khan", "metric", "Dots Forced", "root"),
    ("Extras Conceded by Mitchell Starc", "metric", "Extras Conceded", "root"),
    ("Runs Conceded in Over by Mitchell Starc", "metric", "Runs Conceded in Over", "root"),

    # --- BOWLING FILTERS ---
    ("Jasprit Bumrah wickets against Joe Root", "batter_vs_bowler", "Joe Root", "filters"),
    ("Babar Azam average against Pace", "bowler_type", "Pace", "filters"),
    ("Virat Kohli against Left arm bowlers", "bowler_hand", "Left", "filters"),
    ("Jasprit Bumrah economy in over 19", "over_number", 19, "filters"),
    ("Pat Cummins wickets in Death overs", "match_phase", "Death", "filters"),
    ("Virat Kohli against Left-arm Pace", "batter_vs_bowler_type", "Left-arm Pace", "filters"),
    
    # --- TEMPORAL FILTERS ---
    ("Babar Azam runs since 2019", "start_date", "2019-01-01", "root"),
    ("Virat Kohli average since 2020", "start_date", "2020-01-01", "root"),
    ("Steve Smith average for 3 years", "start_date", "2023-01-01", "root"),
    ("Rohit Sharma runs in 2023", "as_of_date", "2023-12-31", "root")
]

def run_tests():
    passed = 0
    failed = 0
    print("=" * 60)
    print("38 PARAMETER AI PARSER MAPPING TEST")
    print("=" * 60)
    
    report_path = Path(__file__).resolve().parents[2] / 'output' / '38_parameter_test_report.txt'
    
    with open(report_path, 'w') as f:
        f.write("38 PARAMETER AI PARSER MAPPING TEST REPORT\n")
        f.write("=" * 60 + "\n\n")
        
        for i, (claim, key, expected_val, location) in enumerate(TESTS):
            msg = f"Testing Parameter #{i+1}: {key} (Expected: {expected_val})"
            print(msg)
            f.write(msg + "\n")
            f.write(f"Claim: '{claim}'\n")
            
            try:
                # Add delay to avoid rate limiting from Gemini/Groq
                time.sleep(1.5)
                result = parse_claim(claim)
            except Exception as e:
                print(f"  [ERROR] Exception calling LLM: {e}")
                f.write(f"  [FAIL] Exception: {e}\n\n")
                failed += 1
                continue
            
            if location == "root":
                actual = result.get(key)
            else:
                actual = result.get("filters", {}).get(key)
                
            match = False
            if isinstance(expected_val, str) and isinstance(actual, str):
                match = expected_val.lower() in actual.lower() or actual.lower() in expected_val.lower()
            elif isinstance(expected_val, int) and isinstance(actual, (int, float)):
                match = int(actual) == expected_val
            elif isinstance(expected_val, bool) and isinstance(actual, bool):
                match = expected_val == actual
            else:
                match = actual == expected_val
                
            if match:
                print(f"  [PASS] Successfully mapped '{key}' to '{actual}'\n")
                f.write(f"  [PASS] Successfully mapped '{key}' to '{actual}'\n\n")
                passed += 1
            else:
                print(f"  [FAIL] Expected '{expected_val}', but got '{actual}'\n")
                f.write(f"  [FAIL] Expected '{expected_val}', but got '{actual}'\n")
                f.write(f"         Full AI Output: {json.dumps(result)}\n\n")
                failed += 1

        summary = f"\nSUMMARY: {passed} passed, {failed} failed out of {len(TESTS)} total parameters tested.\n"
        print("=" * 60)
        print(summary)
        f.write("=" * 60 + "\n")
        f.write(summary)
        
if __name__ == "__main__":
    run_tests()

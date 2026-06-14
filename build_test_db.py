import os
import sys
import json
import sqlite3
import time
import argparse
import re
from pathlib import Path

# Fix windows console printing issue for box-drawing characters
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

def build_db(dataset_path: str):
    start_time = time.time()
    db_path = "cricket_test.db"
    
    if os.path.exists(db_path):
        os.remove(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA cache_size=-64000;")
    
    cursor.execute("""
        CREATE TABLE deliveries (
            match_id TEXT,
            season TEXT,
            date TEXT,
            venue TEXT,
            city TEXT,
            match_format TEXT,
            team1 TEXT,
            team2 TEXT,
            batting_team TEXT,
            bowling_team TEXT,
            innings INTEGER,
            over INTEGER,
            ball INTEGER,
            batter TEXT,
            non_striker TEXT,
            bowler TEXT,
            runs_batter INTEGER,
            runs_extras INTEGER,
            runs_total INTEGER,
            wicket_kind TEXT,
            player_out TEXT,
            fielder TEXT,
            toss_winner TEXT,
            toss_decision TEXT
        )
    """)
    
    target_formats = {"ODI", "T20I"}
    target_teams = {"Pakistan", "India", "Australia", "England"}
    target_seasons = {"2018", "2019", "2020", "2021", "2022", "2023"}
    
    batch = []
    batch_size = 50000
    
    total_matches = 0
    total_deliveries = 0
    formats_count = {"ODI": 0, "T20I": 0}
    seasons_set = set()
    teams_set = set()
    
    insert_sql = """
        INSERT INTO deliveries VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """
    
    dataset_dir = Path(dataset_path)
    if not dataset_dir.exists():
        print(f"Error: Directory {dataset_path} does not exist.")
        return
        
    for root, _, files in os.walk(dataset_dir):
        for file in files:
            if not file.endswith(".json"):
                continue
            
            file_path = Path(root) / file
            
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except json.JSONDecodeError:
                continue
                
            info = data.get("info", {})
            if not info:
                continue
                
            match_type = info.get("match_type")
            if match_type not in target_formats:
                continue
                
            gender = info.get("gender")
            if gender != "male":
                continue
                
            teams = info.get("teams", [])
            if not set(teams).intersection(target_teams):
                continue
                
            season_val = str(info.get("season", ""))
            years_in_season = set(re.findall(r"\d{4}", season_val))
            if not years_in_season.intersection(target_seasons):
                continue
                
            match_id = file_path.stem
            date = info.get("dates", [None])[0]
            venue = info.get("venue")
            city = info.get("city")
            team1 = teams[0] if len(teams) > 0 else None
            team2 = teams[1] if len(teams) > 1 else None
            toss_winner = info.get("toss", {}).get("winner")
            toss_decision = info.get("toss", {}).get("decision")
            
            match_deliveries = 0
            
            for inn_idx, inn in enumerate(data.get("innings", [])):
                innings_num = inn_idx + 1
                batting_team = inn.get("team")
                
                # Determine bowling team
                bowling_team = None
                if batting_team == team1:
                    bowling_team = team2
                elif batting_team == team2:
                    bowling_team = team1
                else:
                    # Fallback in case team names slightly mismatch or aren't in `teams` array
                    other_teams = [t for t in teams if t != batting_team]
                    bowling_team = other_teams[0] if other_teams else None
                    
                for over_data in inn.get("overs", []):
                    over_num = over_data.get("over")
                    for ball_idx, delivery in enumerate(over_data.get("deliveries", [])):
                        ball_num = ball_idx + 1
                        batter = delivery.get("batter")
                        non_striker = delivery.get("non_striker")
                        bowler = delivery.get("bowler")
                        
                        runs = delivery.get("runs", {})
                        runs_batter = runs.get("batter", 0)
                        runs_extras = runs.get("extras", 0)
                        runs_total = runs.get("total", 0)
                        
                        wickets = delivery.get("wickets", [])
                        if wickets:
                            w = wickets[0]
                            wicket_kind = w.get("kind")
                            player_out = w.get("player_out")
                            fielders = w.get("fielders", [])
                            fielder = fielders[0].get("name") if fielders else None
                        else:
                            wicket_kind = None
                            player_out = None
                            fielder = None
                            
                        row = (
                            match_id, season_val, date, venue, city, match_type, team1, team2,
                            batting_team, bowling_team, innings_num, over_num, ball_num,
                            batter, non_striker, bowler, runs_batter, runs_extras, runs_total,
                            wicket_kind, player_out, fielder, toss_winner, toss_decision
                        )
                        batch.append(row)
                        match_deliveries += 1
                        total_deliveries += 1
                        
                        if len(batch) >= batch_size:
                            cursor.executemany(insert_sql, batch)
                            conn.commit()
                            batch = []
            
            if match_deliveries > 0:
                total_matches += 1
                formats_count[match_type] += 1
                seasons_set.add(season_val)
                teams_set.update(teams)
                
    if batch:
        cursor.executemany(insert_sql, batch)
        conn.commit()
        
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_batter ON deliveries(batter);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bowler ON deliveries(bowler);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_match ON deliveries(match_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_season ON deliveries(season);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_format ON deliveries(match_format);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_innings ON deliveries(innings);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_over ON deliveries(over);")
    
    conn.close()
    
    end_time = time.time()
    build_time = end_time - start_time
    file_size_mb = os.path.getsize(db_path) / (1024 * 1024)
    
    print("┌─────────────────────────────────────────────┐")
    print("│  cricket_test.db build report               │")
    print("├──────────────────────┬──────────────────────┤")
    # Format seasons and teams list to fit nicely or truncate if too long
    seasons_str = ", ".join(sorted(list(seasons_set)))
    if len(seasons_str) > 20:
        seasons_str = seasons_str[:17] + "..."
    
    print(f"│  Total matches       │  {total_matches:<20}│")
    print(f"│  Total deliveries    │  {total_deliveries:<20}│")
    
    fmt_str = f"│  Formats             │  ODI={formats_count['ODI']}, T20I={formats_count['T20I']}"
    print(f"{fmt_str:<46}│")
    
    print(f"│  Seasons             │  {seasons_str:<20}│")
    print(f"│  Teams (unique)      │  {len(teams_set):<20}│")
    
    fs_str = f"│  File size           │  {file_size_mb:.2f} MB"
    print(f"{fs_str:<46}│")
    
    bt_str = f"│  Build time          │  {build_time:.2f}s"
    print(f"{bt_str:<46}│")
    print("└──────────────────────┴──────────────────────┘")
    
    if file_size_mb > 550:
        print("\nWARN: File size > 550 MB. Consider tightening season range to 2020-2023.")
    elif file_size_mb < 100:
        print("\nWARN: File size < 100 MB. Likely filter mismatch - check team name spelling.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build test database from Cricsheet JSON")
    parser.add_argument("--dataset-path", default="./Dataset", help="Path to Dataset directory")
    args = parser.parse_args()
    
    build_db(args.dataset_path)

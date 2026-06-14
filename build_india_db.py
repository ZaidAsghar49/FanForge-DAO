import json
import sqlite3
import os
import time
import sys
import argparse
from pathlib import Path

def main():
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="Build cricket_india.db from Cricsheet JSON files.")
    parser.add_argument("--dataset-path", default="./Dataset/Matches", help="Path to Cricsheet JSON matches directory")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_path)
    db_path = Path("cricket_india.db")

    if db_path.exists():
        os.remove(db_path)

    start_time = time.time()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA cache_size = -64000;")
    cur.execute("PRAGMA synchronous = NORMAL;")

    cur.execute("""
        CREATE TABLE deliveries (
            match_id TEXT,
            season TEXT,
            date TEXT,
            venue TEXT,
            city TEXT,
            match_format TEXT,
            gender TEXT,
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

    total_matches = 0
    total_deliveries = 0
    formats = {"ODI": 0, "T20I": 0, "IT20": 0, "Test": 0}
    genders = {"male": 0, "female": 0}
    seasons = set()
    opponents = set()

    batch = []
    batch_size = 50000

    if not dataset_dir.exists() or not dataset_dir.is_dir():
        print(f"Error: Dataset directory '{dataset_dir}' not found.")
        sys.exit(1)

    # Step 1 - SCAN
    for json_file in dataset_dir.rglob("*.json"):
        with open(json_file, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                continue

        info = data.get("info", {})
        
        match_type = info.get("match_type")
        if match_type not in {"ODI", "T20I", "IT20", "Test"}:
            continue

        teams = info.get("teams", [])
        if "India" not in teams:
            continue

        # Step 2 - PARSE
        match_id = json_file.stem
        season_val = info.get("season")
        season = str(season_val) if season_val is not None else None
        
        dates = info.get("dates", [])
        date = str(dates[0]) if dates else None
        
        venue = info.get("venue")
        city = info.get("city")
        gender = info.get("gender")
        
        team1 = str(teams[0]) if len(teams) > 0 else None
        team2 = str(teams[1]) if len(teams) > 1 else None
        
        toss_winner = info.get("toss", {}).get("winner")
        toss_decision = info.get("toss", {}).get("decision")

        total_matches += 1
        formats[match_type] = formats.get(match_type, 0) + 1
        if gender:
            genders[gender] = genders.get(gender, 0) + 1
        if season:
            seasons.add(season)

        for team in teams:
            if team != "India":
                opponents.add(team)

        innings_list = data.get("innings", [])
        for i, inning in enumerate(innings_list):
            innings_idx = i + 1
            batting_team = inning.get("team")
            
            # Resolve bowling team
            if batting_team == team1:
                bowling_team = team2
            elif batting_team == team2:
                bowling_team = team1
            else:
                bowling_team = None
                
            overs = inning.get("overs", [])
            for over_data in overs:
                over_num = over_data.get("over")
                deliveries = over_data.get("deliveries", [])
                
                for ball_idx, delivery in enumerate(deliveries):
                    ball_num = ball_idx + 1
                    batter = delivery.get("batter")
                    non_striker = delivery.get("non_striker")
                    bowler = delivery.get("bowler")
                    
                    runs = delivery.get("runs", {})
                    runs_batter = runs.get("batter", 0)
                    runs_extras = runs.get("extras", 0)
                    runs_total = runs.get("total", 0)
                    
                    wickets = delivery.get("wickets", [])
                    wicket_kind = None
                    player_out = None
                    fielder = None
                    
                    if wickets:
                        first_wicket = wickets[0]
                        wicket_kind = first_wicket.get("kind")
                        player_out = first_wicket.get("player_out")
                        
                        fielders = first_wicket.get("fielders", [])
                        if fielders and len(fielders) > 0:
                            fielder = fielders[0].get("name")
                            
                    batch.append((
                        match_id, season, date, venue, city, match_type, gender,
                        team1, team2, batting_team, bowling_team, innings_idx,
                        over_num, ball_num, batter, non_striker, bowler,
                        runs_batter, runs_extras, runs_total,
                        wicket_kind, player_out, fielder,
                        toss_winner, toss_decision
                    ))
                    total_deliveries += 1
                    
                    # Step 3 - LOAD (batch)
                    if len(batch) >= batch_size:
                        cur.executemany("INSERT INTO deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                        batch = []

    if batch:
        cur.executemany("INSERT INTO deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)

    # Step 4 - INDEX
    cur.execute("CREATE INDEX idx_batter ON deliveries(batter);")
    cur.execute("CREATE INDEX idx_bowler ON deliveries(bowler);")
    cur.execute("CREATE INDEX idx_match ON deliveries(match_id);")
    cur.execute("CREATE INDEX idx_season ON deliveries(season);")
    cur.execute("CREATE INDEX idx_format ON deliveries(match_format);")
    cur.execute("CREATE INDEX idx_innings ON deliveries(innings);")
    cur.execute("CREATE INDEX idx_over ON deliveries(over);")
    cur.execute("CREATE INDEX idx_venue ON deliveries(venue);")
    cur.execute("CREATE INDEX idx_date ON deliveries(date);")

    conn.commit()
    conn.close()

    # Step 5 - VALIDATE & REPORT
    build_time = int(time.time() - start_time)
    file_size_mb = db_path.stat().st_size / (1024 * 1024)

    if seasons:
        valid_seasons = sorted(list(seasons), key=lambda x: str(x))
        min_season = valid_seasons[0]
        max_season = valid_seasons[-1]
    else:
        min_season = "N/A"
        max_season = "N/A"

    print("┌─────────────────────────────────────────────────┐")
    print("│  cricket_india.db  build report                 │")
    print("├───────────────────────────┬─────────────────────┤")
    print(f"│  Total matches            │  {total_matches:<19}│")
    print(f"│  Total deliveries         │  {total_deliveries:<19}│")
    
    f1 = f"ODI={formats.get('ODI',0)} T20I={formats.get('T20I',0)}"
    f2 = f"IT20={formats.get('IT20',0)} Test={formats.get('Test',0)}"
    print(f"│  Formats                  │  {f1:<19}│")
    print(f"│                           │  {f2:<19}│")
    
    gs = f"male={genders.get('male',0)} female={genders.get('female',0)}"
    print(f"│  Gender split             │  {gs:<19}│")
    
    sr = f"{min_season} \u2013 {max_season}"
    print(f"│  Season range             │  {sr:<19}│")
    
    print(f"│  Unique opponents         │  {len(opponents):<19}│")
    
    fs = f"{file_size_mb:.1f} MB"
    print(f"│  File size                │  {fs:<19}│")
    
    bt = f"{build_time}s"
    print(f"│  Build time               │  {bt:<19}│")
    print("└───────────────────────────┴─────────────────────┘")

if __name__ == "__main__":
    main()

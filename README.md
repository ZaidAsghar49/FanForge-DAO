---
title: Cricket Truth O Meter
emoji: 🏏
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---
# CricketTruth AI

> A fact-checking engine that verifies natural language claims about cricket statistics using ball-by-ball data from 20,000+ Cricsheet JSON files.

---

## Quick Start

```bash
# 1. Run the Truth-O-Meter
python scripts/analysis/validate_model.py "Virat Kohli average in Australia against Pace"

# 2. Test player stat cards
python scripts/analysis/test_player_stats.py

# 3. Rebuild the player resolution cache
python scripts/identity/fuzzy_identity_engine.py --audit

# 4. Migrate to fast storage (Parquet + SQLite)
python scripts/pipeline/migrate_to_parquet.py

# 5. Validate data integrity (spot-check)
python scripts/pipeline/data_integrity_validator.py --limit 100
```

---

## Project Structure

```
AI/
├── scripts/
│   ├── pipeline/              # Data ingestion & transformation
│   │   ├── extract_data.py              # JSON -> matches.csv (10.6M deliveries)
│   │   ├── city_map.py                  # City -> Country lookup
│   │   ├── migrate_to_parquet.py        # CSV -> Parquet + SQLite
│   │   ├── data_integrity_validator.py  # Run-sum / wicket checks vs JSON headers
│   │   └── cricsheet_ingestion_engine.py
│   │
│   ├── identity/              # Player name resolution
│   │   ├── identity_engine.py           # Core resolver (RapidFuzz, used by validate_model)
│   │   ├── fuzzy_identity_engine.py     # Deep resolver + audit CLI
│   │   ├── check_player_mappings.py     # Cricket.csv -> DB audit
│   │   ├── create_bowler_db.py          # Spin/Pace classifier
│   │   └── refine_bowlers.py            # Re-classify bowlers using DB reference
│   │
│   └── analysis/              # AI query layer
│       ├── validate_model.py            # Truth-O-Meter (main entry point)
│       ├── ai_parser.py                 # Gemini / Cohere NLP parser
│       └── test_player_stats.py         # Random player stat cards
│
├── Dataset/
│   ├── Matches/               # ~20,000 Cricsheet JSON files
│   └── Players/
│       ├── players_data_with_all_info.csv  # 17,385 canonical players
│       ├── cricketers.csv                  # Kaggle player list
│       └── teams.csv
│
├── output/                    # Generated reports (gitignore-safe)
│   ├── mapped_players.csv
│   ├── ambiguous_players.csv
│   ├── unmapped_players.csv
│   ├── audit_report.csv
│   ├── integrity_report.json
│   ├── integrity_failures.csv
│   ├── resolution_cache.json
│   └── ingestion_report.json
│
├── matches.csv                # 900 MB - 10.6M ball-by-ball deliveries
├── matches.parquet            # ~120 MB compressed (generate with migrate_to_parquet.py)
├── cricket.db                 # SQLite with 7 indexes (generate with migrate_to_parquet.py)
├── bowlers.csv                # Spin / Pace classification per bowler
├── .env                       # GEMINI_API_KEY, COHERE_API_KEY
└── README.md
```

---

## Example Queries

```bash
python scripts/analysis/validate_model.py "Virat Kohli average in Australia against Pace"
# Truth: 43.41

python scripts/analysis/validate_model.py "Virat Kohli average in Australia against Australia"
# Truth: 49.47

python scripts/analysis/validate_model.py "Rohit Sharma runs in India"
python scripts/analysis/validate_model.py "Jasprit Bumrah wickets in England"
python scripts/analysis/validate_model.py "Steve Smith average"
```

---

## Data Layer

| File | Size | Description |
|------|------|-------------|
| `matches.csv` | ~900 MB | Source — full ball-by-ball data |
| `matches.parquet` | ~120 MB | Recommended for queries (7× smaller, column-skip) |
| `cricket.db` | ~450 MB | SQLite with indexes — ideal for dashboards |

### Querying via Parquet
```python
import pandas as pd
df = pd.read_parquet("matches.parquet",
                     filters=[("batter", "==", "V Kohli")])
print(df["runs_batter"].sum())
```

### Querying via SQLite
```sql
SELECT SUM(runs_batter) FROM deliveries WHERE batter = 'V Kohli';
```

---

## Architecture

```
Natural Language Query
        |
        v
  ai_parser.py  (Gemini / Cohere)
        |
        v
  validate_model.py
        |- identity_engine.py  (RapidFuzz name resolution)
        |- city_map.py         (Melbourne -> Australia)
        |- matches.parquet     (10.6M deliveries)
        |- bowlers.csv         (Spin / Pace per bowler)
        |
        v
  Truth-O-Meter Output
```

import sys; sys.path.insert(0, '.')
import duckdb
import pandas as pd
from pathlib import Path
from scripts.analysis.prediction.feature_builder import build_innings_features, encode_categorical_features

db_path = 'cricket.db'
safe = Path(db_path).as_posix()
con = duckdb.connect()
con.execute('INSTALL sqlite; LOAD sqlite;')
# Fetch actual data matching what validate_model sends to prediction
q = "SELECT * FROM sqlite_scan('{}', 'deliveries') WHERE batter IN ('Virat Kohli','V Kohli') AND competition IN ('World Cup','ICC Cricket World Cup','Asia Cup','ICC Champions Trophy') LIMIT 500".format(safe)
df = con.execute(q).fetch_df()
con.close()
print('ROWS:', len(df))
print('COLS:', [c for c in df.columns])

innings = build_innings_features(df, 'Virat Kohli', is_batting=True)
print('INNINGS:', len(innings))
if len(innings) > 0:
    enc = encode_categorical_features(innings)
    print('ENCODED COLS:', enc.columns.tolist())
    print(enc.head(3).to_string())

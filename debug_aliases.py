import pandas as pd
import json
import duckdb
from scripts.analysis.validate_model import _load_subject_dataframe, apply_filters_from_plan, _get_engine
from scripts.analysis.query_planner import FilterSet

engine = _get_engine()
subject = 'Shaheen Shah Afridi'
subject_col = 'bowler'
metric = 'Wickets'
filters = {'format': 'T20I', '_is_batting_role': False}

df_full = _load_subject_dataframe(subject_col, subject, engine, metric, filters)
print(f"Loaded {len(df_full) if df_full is not None else 'None'} rows")
print("Columns:", list(df_full.columns))

fs = FilterSet()
fs.is_batting = False
fs.match_types = ["IT20"]
fs.competitions = None
fs.canonical_name = subject

df_fil = apply_filters_from_plan(df_full, fs, engine)
print(f"Post filter rows: {len(df_fil)}")

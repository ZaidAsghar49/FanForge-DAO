import pandas as pd
from validate_model import _load_subject_dataframe, apply_filters, EXECUTION_MODE

def test_engine_rules():
    # Mock dataframe with some columns
    df = pd.DataFrame({
        "batter": ["Virat Kohli", "Steve Smith"],
        "venue_name": ["Melbourne Cricket Ground", "Adelaide Oval No 2"],
        "competition": ["The Ashes", "ICC Men's T20 World Cup"],
        "match_type": ["Test", "IT20"],
        "date": ["2023-01-01", "2025-10-10"],
        "day_night": ["Day", "Night"],
        "innings": [1, 3],
        "home_team": ["Australia", "Australia"],
        "batting_team": ["India", "Australia"],
        "bowling_team": ["Australia", "India"],
        "neutral_venue": [0, 0]
    })
    
    # 1. rapidfuzz venue_name (The G -> Melbourne Cricket Ground)
    filters = {"venue_name": "the g"}
    df_out = apply_filters(df, filters, is_batting=True)
    assert len(df_out) == 1 and df_out.iloc[0]["venue_name"] == "Melbourne Cricket Ground", "Venue fuzzy match failed"

    # 2. Deterministic T20 block
    filters = {"format": "T20"}
    try:
        apply_filters(df, filters, is_batting=True)
        assert False, "T20 block failed to raise NotImplementedError"
    except NotImplementedError:
        pass

    # 3. Innings > 2 for ODI
    filters = {"format": "ODI", "innings": 3}
    try:
        apply_filters(df, filters, is_batting=True)
        assert False, "Innings > 2 for ODI failed to raise ValueError"
    except ValueError:
        pass

    # 4. dynamic date for "recent form"
    filters = {"season": "recent form"}
    df_out = apply_filters(df, filters, is_batting=True)
    assert len(df_out) == 1 and df_out.iloc[0]["date"] == "2025-10-10", "Recent form dynamic date failed"

    print("All engine rules tests passed!")

if __name__ == "__main__":
    test_engine_rules()

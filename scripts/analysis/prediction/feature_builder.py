import pandas as pd
import numpy as np

def build_innings_features(df: pd.DataFrame, subject: str, is_batting: bool = True) -> pd.DataFrame:
    """
    Transforms ball-by-ball DataFrame into an aggregated innings-level DataFrame
    with engineered features for prediction.
    """
    # Defensive check
    if df.empty:
        return pd.DataFrame()

    df = df.copy()

    # Sort chronologically
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    else:
        df["date"] = pd.NaT

    # To group by innings properly across matches, we need a unique match identifier
    # If match_id is not strictly present, date + venue + innings works as a proxy
    if "match_id" not in df.columns:
        # Fallback pseudo-match ID
        df["match_id"] = df["date"].astype(str) + "_" + df.get("venue_name", "Unknown")

    group_cols = ["match_id", "date"]
    
    # We want to keep some match metadata
    metadata_cols = ["venue_name", "competition", "home_team"]
    for col in metadata_cols:
        if col not in df.columns:
            df[col] = "Unknown"
            
    if "neutral_venue" not in df.columns:
        df["neutral_venue"] = 0

    if is_batting:
        if "batting_team" not in df.columns: df["batting_team"] = "Unknown"
        if "bowling_team" not in df.columns: df["bowling_team"] = "Unknown"
        
        # Aggregate stats per innings
        aggs = {
            "runs_batter": "sum",
            "is_wicket": "sum",  # if any wicket is for the batter
            # "venue_name": "first",
            "bowling_team": "first",
            "batting_team": "first",
            "neutral_venue": "first"
        }
        
        innings_df = df.groupby(group_cols, as_index=False).agg(aggs).sort_values("date")
        
        # Count balls faced (number of rows per match_id for the batter)
        balls_faced = df.groupby(group_cols).size().reset_index(name="balls_faced")
        innings_df = innings_df.merge(balls_faced, on=group_cols, how="left")
        
        innings_df.rename(columns={"runs_batter": "runs", "bowling_team": "opposition"}, inplace=True)
        # 1 if out, 0 if not out
        innings_df["is_out"] = (innings_df["is_wicket"] > 0).astype(int)
        innings_df["strike_rate"] = np.where(innings_df["balls_faced"] > 0, 
                                            (innings_df["runs"] / innings_df["balls_faced"]) * 100, 
                                            0)
        
        # Feature Engineering: Rolling Form (last 5 & 10 innings)
        # Shift(1) so we predict the current innings using ONLY past innings
        innings_df["runs_last_5"] = innings_df["runs"].rolling(5, min_periods=1).mean().shift(1).fillna(0)
        innings_df["runs_last_10"] = innings_df["runs"].rolling(10, min_periods=1).mean().shift(1).fillna(0)
        innings_df["sr_last_5"] = innings_df["strike_rate"].rolling(5, min_periods=1).mean().shift(1).fillna(0)
        
        innings_df["target_50"] = (innings_df["runs"] >= 50).astype(int)
        innings_df["target_100"] = (innings_df["runs"] >= 100).astype(int)
        
    else:
        if "batting_team" not in df.columns: df["batting_team"] = "Unknown"
        if "bowling_team" not in df.columns: df["bowling_team"] = "Unknown"
        
        # Bowling metrics
        aggs = {
            "runs_total": "sum",
            "is_wicket": "sum",
            "batting_team": "first",
            "bowling_team": "first",
            "neutral_venue": "first"
        }
        innings_df = df.groupby(group_cols, as_index=False).agg(aggs).sort_values("date")
        
        balls_bowled = df.groupby(group_cols).size().reset_index(name="balls_bowled")
        innings_df = innings_df.merge(balls_bowled, on=group_cols, how="left")
        
        innings_df.rename(columns={"runs_total": "runs_conceded", "is_wicket": "wickets", "batting_team": "opposition"}, inplace=True)
        
        innings_df["economy"] = np.where(innings_df["balls_bowled"] > 0,
                                        (innings_df["runs_conceded"] / innings_df["balls_bowled"]) * 6,
                                        0)
        
        innings_df["wickets_last_5"] = innings_df["wickets"].rolling(5, min_periods=1).mean().shift(1).fillna(0)
        innings_df["econ_last_5"] = innings_df["economy"].rolling(5, min_periods=1).mean().shift(1).fillna(0)
        
        innings_df["target_runs"] = innings_df["runs_conceded"]
        innings_df["target_wickets"] = innings_df["wickets"]

    # Drop the first row since rolling features for it will be 0 (no history)
    # But for a very small dataset, keep it. 
    return innings_df

def encode_categorical_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Encodes standard categorical features like opposition and venue
    using frequency encoding to keep dimensionality reasonable.
    """
    if df.empty:
        return df
    
    encoded = df.copy()
    
    if "opposition" in encoded.columns:
        freq = encoded["opposition"].value_counts(normalize=True)
        encoded["opp_freq"] = encoded["opposition"].map(freq).fillna(0)
        
    # Pressure index approximation (chasing vs setting)
    # This requires toss/innings info, but using simplified version: 'innings' column if available
    if "innings" in encoded.columns:
        encoded["is_chasing"] = (encoded["innings"] == 2).astype(int)
    else:
        encoded["is_chasing"] = 0
        
    encoded.fillna(0, inplace=True)
    return encoded

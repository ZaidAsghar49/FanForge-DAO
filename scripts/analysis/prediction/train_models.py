import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

def train_batting_models(df: pd.DataFrame):
    """
    Trains multiple models for a batter:
    1. Linear Regression (for expected average / runs baseline)
    2. Gradient Boosting (for non-linear strike rate & expected runs)
    3. Logistic Regression (for probability of 50+ & 100+)
    """
    models = {}
    metrics = {}
    
    # Needs at least 10 innings to predict meaningfully
    if len(df) < 10:
        return None, None
        
    features = ["runs_last_5", "runs_last_10", "sr_last_5", "is_chasing", "neutral_venue"]
    # Add opp_freq if it exists
    if "opp_freq" in df.columns: features.append("opp_freq")
    
    # Remove NaN or inf
    clean_df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=features + ["runs", "strike_rate"])
    
    X = clean_df[features]
    
    # 1. Expected Runs (Linear & GB)
    y_runs = clean_df["runs"]
    lr_runs = LinearRegression()
    lr_runs.fit(X, y_runs)
    models["runs_lr"] = lr_runs
    
    gb_runs = HistGradientBoostingRegressor(max_iter=50, max_leaf_nodes=15)
    gb_runs.fit(X, y_runs)
    models["runs_gb"] = gb_runs
    
    metrics["runs_mae_lr"] = mean_absolute_error(y_runs, lr_runs.predict(X))
    metrics["runs_mae_gb"] = mean_absolute_error(y_runs, gb_runs.predict(X))
    
    # 2. Strike Rate (GB)
    y_sr = clean_df["strike_rate"]
    gb_sr = HistGradientBoostingRegressor(max_iter=50, max_leaf_nodes=15)
    gb_sr.fit(X, y_sr)
    models["sr_gb"] = gb_sr
    
    # 3. Probability Models (Logistic)
    y_50 = clean_df["target_50"]
    if y_50.sum() > 0: # Check if they ever scored 50
        log_50 = LogisticRegression(max_iter=200, class_weight='balanced')
        log_50.fit(X, y_50)
        models["prob_50"] = log_50
    
    y_100 = clean_df["target_100"]
    if y_100.sum() > 0:
        log_100 = LogisticRegression(max_iter=200, class_weight='balanced')
        log_100.fit(X, y_100)
        models["prob_100"] = log_100

    return models, metrics

def train_bowling_models(df: pd.DataFrame):
    """
    Trains models for a bowler: Expected Wickets, Expected Economy.
    """
    models = {}
    metrics = {}
    
    if len(df) < 10:
        return None, None
        
    features = ["wickets_last_5", "econ_last_5", "is_chasing", "neutral_venue"]
    if "opp_freq" in df.columns: features.append("opp_freq")
    
    clean_df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=features + ["wickets", "economy"])
    X = clean_df[features]
    
    # 1. Expected Wickets
    y_w = clean_df["wickets"]
    gb_w = HistGradientBoostingRegressor(max_iter=50, max_leaf_nodes=15)
    gb_w.fit(X, y_w)
    models["wickets_gb"] = gb_w
    metrics["wickets_mae"] = mean_absolute_error(y_w, gb_w.predict(X))
    
    # 2. Expected Economy
    y_econ = clean_df["economy"]
    gb_econ = HistGradientBoostingRegressor(max_iter=50, max_leaf_nodes=15)
    gb_econ.fit(X, y_econ)
    models["econ_gb"] = gb_econ
    
    return models, metrics

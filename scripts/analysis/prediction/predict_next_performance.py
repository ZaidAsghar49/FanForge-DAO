import pandas as pd
import numpy as np

def predict_next_batting(models, df_recent, target_context=None):
    """
    Predicts next performance using trained models and latest form.
    target_context is a dict describing the intended match context (opposition, chasing, etc.)
    """
    if models is None or df_recent.empty:
        return {"error": "Not enough data or models unavailable"}

    recent_row = df_recent.iloc[-1].copy()
    
    # Feature inputs
    X_input = {
        "runs_last_5": recent_row["runs_last_5"],
        "runs_last_10": recent_row["runs_last_10"],
        "sr_last_5": recent_row["sr_last_5"],
        "is_chasing": target_context.get("is_chasing", recent_row.get("is_chasing", 0)),
        "neutral_venue": target_context.get("neutral_venue", recent_row.get("neutral_venue", 0))
    }
    
    if "opp_freq" in df_recent.columns:
        X_input["opp_freq"] = target_context.get("opp_freq", recent_row.get("opp_freq", 0))

    X_df = pd.DataFrame([X_input])
    
    predictions = {}
    
    # Run predictions
    if "runs_lr" in models and "runs_gb" in models:
        pred_lr = max(0, models["runs_lr"].predict(X_df)[0])
        pred_gb = max(0, models["runs_gb"].predict(X_df)[0])
        # Blend the two
        predictions["expected_runs"] = round((pred_lr + pred_gb) / 2)
        
    if "sr_gb" in models:
        pred_sr = models["sr_gb"].predict(X_df)[0]
        predictions["expected_sr"] = float(max(0, round(pred_sr, 1)))
        
    if "prob_50" in models:
        try:
            prob = models["prob_50"].predict_proba(X_df)[0][1] * 100
        except IndexError:
            prob = 0
        predictions["prob_50"] = round(prob)
    else:
        predictions["prob_50"] = 0
        
    if "prob_100" in models:
        try:
            prob = models["prob_100"].predict_proba(X_df)[0][1] * 100
        except IndexError:
            prob = 0
        predictions["prob_100"] = round(prob)
    else:
        predictions["prob_100"] = 0

    return predictions

def predict_next_bowling(models, df_recent, target_context=None):
    if models is None or df_recent.empty:
        return {"error": "Not enough data or models unavailable"}

    recent_row = df_recent.iloc[-1].copy()
    
    X_input = {
        "wickets_last_5": recent_row["wickets_last_5"],
        "econ_last_5": recent_row["econ_last_5"],
        "is_chasing": target_context.get("is_chasing", recent_row.get("is_chasing", 0)),
        "neutral_venue": target_context.get("neutral_venue", recent_row.get("neutral_venue", 0))
    }
    if "opp_freq" in df_recent.columns:
        X_input["opp_freq"] = target_context.get("opp_freq", recent_row.get("opp_freq", 0))
    
    X_df = pd.DataFrame([X_input])
    
    predictions = {}
    if "wickets_gb" in models:
        predictions["expected_wickets"] = float(round(max(0, models["wickets_gb"].predict(X_df)[0]), 1))
        
    if "econ_gb" in models:
        predictions["expected_economy"] = float(round(max(0, models["econ_gb"].predict(X_df)[0]), 2))
        
    return predictions

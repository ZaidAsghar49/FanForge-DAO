import pandas as pd
from scripts.analysis.prediction.feature_builder import build_innings_features, encode_categorical_features
from scripts.analysis.prediction.train_models import train_batting_models, train_bowling_models
from scripts.analysis.prediction.predict_next_performance import predict_next_batting, predict_next_bowling

def run_prediction_pipeline(df_subject_full: pd.DataFrame, subject: str, is_batting: bool, filters: dict):
    """
    Orchestrates the predictive analysis pipeline.
    Takes the full historical dataset for the player, builds features,
    trains models on the fly, and predicts their next performance 
    based on the current context (filters).
    """
    if df_subject_full is None or df_subject_full.empty:
        return {"error": "No historical data available for prediction."}

    # 1. Feature Engineering
    df_features = build_innings_features(df_subject_full, subject, is_batting)
    df_encoded = encode_categorical_features(df_features)
    
    if len(df_encoded) < 10:
        return {"error": f"Not enough innings ({len(df_encoded)}) for reliable predictions (need at least 10)."}

    # 2. Extract Target Context from filters
    # Estimate the scenario the user is asking about
    target_context = {"is_chasing": 0, "neutral_venue": 0, "opp_freq": 0.05} # default small freq
    
    innings = filters.get("innings")
    if innings is not None and str(innings) == "2":
        target_context["is_chasing"] = 1
        
    neutral = filters.get("neutral_venue")
    if neutral:
        target_context["neutral_venue"] = 1
        
    opp = filters.get("opposition")
    if opp and "opposition" in df_encoded.columns:
        # Check freq
        opp_mask = df_encoded["opposition"].str.lower().str.contains(opp.lower(), na=False)
        if opp_mask.any():
            target_context["opp_freq"] = opp_mask.mean()

    # 3. Model Training & Prediction
    if is_batting:
        models, metrics = train_batting_models(df_encoded)
        if models is None:
            return {"error": "Training failed."}
        preds = predict_next_batting(models, df_encoded, target_context)
        
        # Determine confidence based on MAE
        mae = metrics.get('runs_mae_gb', 20)
        confidence = max(0, min(100, 100 - mae * 1.5))
        preds["confidence"] = round(confidence)
        
        # Calculate Expected Average as an interval
        expected_runs = preds.get('expected_runs', 0)
        margin = max(3, expected_runs * 0.1) # 10% margin
        preds["expected_average_range"] = f"{round(expected_runs - margin)}–{round(expected_runs + margin)}"
        preds["expected_runs"] = expected_runs
        
    else:
        models, metrics = train_bowling_models(df_encoded)
        if models is None:
            return {"error": "Training failed."}
        preds = predict_next_bowling(models, df_encoded, target_context)
        
        mae = metrics.get('wickets_mae', 1.0)
        confidence = max(0, min(100, 100 - mae * 20))
        preds["confidence"] = round(confidence)

    return preds

def format_prediction_output(preds: dict, is_batting: bool) -> str:
    """
    Returns a formatted string for terminal output.
    """
    if "error" in preds:
         return f"\n[Predictive Analysis] \n  ❌ {preds['error']}\n"
         
    out = []
    out.append("\n==================================================")
    out.append("  PREDICTIVE ANALYSIS (Next Match Estimates)")
    out.append("==================================================")
    
    if is_batting:
        out.append(f"  Expected Average Range : {preds.get('expected_average_range', '?')}")
        out.append(f"  Expected Strike Rate   : {preds.get('expected_sr', '?')}")
        out.append(f"  Probability of 50+     : {preds.get('prob_50', 0)}%")
        out.append(f"  Probability of 100+    : {preds.get('prob_100', 0)}%")
        out.append(f"  Expected Runs          : {preds.get('expected_runs', '?')}")
    else:
        out.append(f"  Expected Wickets       : {preds.get('expected_wickets', '?')}")
        out.append(f"  Expected Economy       : {preds.get('expected_economy', '?')}")
        
    out.append(f"  Model Confidence Score : {preds.get('confidence', '?')}%")
    out.append("==================================================\n")
    return "\n".join(out)
